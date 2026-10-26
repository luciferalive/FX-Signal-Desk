"""The ONE place that may approve a trade. Strategies and the arbitration engine only PROPOSE; nothing reaches the
broker unless RiskManager.evaluate() approves it. A rejection always carries a plain-words reason.

All limits come from the active profile (demo or live). The checks do not stop at the first failure, so a rejected
trade lists every reason at once."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import spread_pips
from ..config import load as load_main_config
from .exits import plan_exits
from .filters import (current_session, market_closed_block, news_block, quote_stale, rollover_block, session_block)
from .models import AccountInfo, BrokerError, PendingOrder, Position, Quote, SymbolSpec
from .sizing import floor_to_step, remaining_risk, size_position
from .state import StateStore, Trade
from .timeutil import trading_day_key, trading_week_key


@dataclass
class Proposal:
    """A candidate trade. No strategy may talk to the broker, they all end up as one of these."""
    signal_id: str
    symbol: str
    side: int                       # +1 buy, -1 sell
    strategy: str                   # strategy key; consolidated proposals list all sources in `sources`
    family: str
    style: str                      # scalp | intraday | swing
    entry: float
    sl: float
    tps: list[float]
    time: pd.Timestamp
    confidence: float = 0.0
    atr: float | None = None
    vol_ratio: float | None = None
    explanation: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    allow_widen: bool = False


@dataclass
class Context:
    account: AccountInfo
    spec: SymbolSpec
    quote: Quote
    positions: list[Position]
    pending: list[PendingOrder]
    trades: list[Trade]             # our open trade records
    specs: dict[str, SymbolSpec]    # spec of every symbol that has a position or order
    store: StateStore
    now: pd.Timestamp
    events: list[dict] | None = None
    broker: object = None           # used only to ask for the margin requirement


@dataclass
class Decision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    volume: float = 0.0
    risk_money: float = 0.0
    exit_volumes: list[float | None] = field(default_factory=list)
    exec_price: float = 0.0
    checks: dict = field(default_factory=dict)   # the numbers behind the decision, for the audit log

    def summary(self) -> str:
        return "APPROVED" if self.approved else " ".join(self.reasons)


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ----------------------------------------------------------------------------- loss baselines and locks
def update_baselines(store: StateStore, equity: float, now, cfg: dict) -> None:
    """Remember the equity at the start of each trading day and week (broker timezone, rollover hour)."""
    sc = cfg["sessions"]
    d = trading_day_key(now, sc["broker_tz"], sc["rollover_hour"])
    w = trading_week_key(now, sc["broker_tz"], sc["rollover_hour"])
    if store.data["day_key"] != d:
        store.data["day_key"], store.data["day_start_equity"] = d, float(equity)
    if store.data["week_key"] != w:
        store.data["week_key"], store.data["week_start_equity"] = w, float(equity)


def loss_status(store: StateStore, equity: float, cfg: dict) -> dict:
    """Equity already contains realized P&L, unrealized P&L, commission and financing, so it is the right measure.
    (Deposits and withdrawals during the day would distort it: avoid moving money while the bot runs.)"""
    r = cfg["risk"]
    ds, ws = store.data["day_start_equity"] or equity, store.data["week_start_equity"] or equity
    day_loss = max(0.0, (ds - equity) / ds * 100) if ds else 0.0
    week_loss = max(0.0, (ws - equity) / ws * 100) if ws else 0.0
    return dict(day_start=ds, week_start=ws, day_pnl=round(equity - ds, 2), week_pnl=round(equity - ws, 2),
                day_loss_pct=round(day_loss, 3), week_loss_pct=round(week_loss, 3),
                day_limit_hit=day_loss >= r["max_daily_loss_pct"], week_limit_hit=week_loss >= r["max_weekly_loss_pct"],
                day_risk_left=round(max(0.0, ds * r["max_daily_loss_pct"] / 100 - max(0.0, ds - equity)), 2))


def lock_active(store: StateStore) -> dict | None:
    """The persistent risk lock. A daily lock ends with the trading day, a weekly lock with the trading week."""
    return store.data.get("lock")


# ----------------------------------------------------------------------------- the manager
class RiskManager:
    def __init__(self, cfg: dict, main_cfg: dict | None = None):
        self.cfg = cfg
        self.main = main_cfg or load_main_config()

    # ---------------- risk accounting
    def position_risk(self, p: Position, spec: SymbolSpec, equity: float) -> float:
        r = self.cfg["risk"]
        return remaining_risk(p.side, p.volume, p.open_price, p.sl, spec, r["slippage_pips"],
                              unprotected=equity * r["no_stop_risk_pct"] / 100)

    def order_risk(self, o: PendingOrder, spec: SymbolSpec, equity: float) -> float:
        r = self.cfg["risk"]
        return remaining_risk(o.side, o.volume, o.price, o.sl, spec, r["slippage_pips"],
                              unprotected=equity * r["no_stop_risk_pct"] / 100)

    def open_risk(self, ctx: Context) -> float:
        """Total money at risk across ALL broker positions (ours and manual) and pending orders, from current stops."""
        total = 0.0
        for p in ctx.positions:
            total += self.position_risk(p, ctx.specs[p.symbol], ctx.account.equity)
        for o in ctx.pending:
            total += self.order_risk(o, ctx.specs[o.symbol], ctx.account.equity)
        return total

    @staticmethod
    def _currency_dirs(symbol: str, side: int) -> list[tuple[str, int]]:
        base, quote = symbol[:3], symbol[3:6]
        return [(base, side), (quote, -side)]

    def currency_risk(self, ctx: Context) -> dict[tuple[str, int], float]:
        out: dict[tuple[str, int], float] = {}
        items = [(p.symbol, p.side, self.position_risk(p, ctx.specs[p.symbol], ctx.account.equity)) for p in ctx.positions]
        items += [(o.symbol, o.side, self.order_risk(o, ctx.specs[o.symbol], ctx.account.equity)) for o in ctx.pending]
        for sym, side, risk in items:
            for key in self._currency_dirs(sym, side):
                out[key] = out.get(key, 0.0) + risk
        return out

    # ---------------- the decision
    def evaluate(self, p: Proposal, ctx: Context) -> Decision:
        r, ex = self.cfg["risk"], self.cfg["execution"]
        d = Decision(False)
        why: list[str] = []
        eq = ctx.account.equity
        now = _utc(ctx.now)
        store = ctx.store
        side = p.side
        q, spec = ctx.quote, ctx.spec
        exec_px = q.ask if side > 0 else q.bid     # the price a market order really fills at
        d.exec_price = exec_px
        st = loss_status(store, eq, self.cfg)
        d.checks["loss"] = st

        # --- 1. controls and locks
        if store.data["stopped"]:
            why.append("REJECTED: automated trading is stopped (STOP ALL TRADING is active).")
        if store.data["paused"]:
            why.append("REJECTED: new entries are paused.")
        lk = lock_active(store)
        if lk:
            why.append(f"REJECTED: risk lock active ({lk['reason']}).")
        if st["day_limit_hit"]:
            why.append(f"REJECTED: daily loss limit reached ({st['day_loss_pct']:.2f}% of {r['max_daily_loss_pct']}%).")
        if st["week_limit_hit"]:
            why.append(f"REJECTED: weekly loss limit reached ({st['week_loss_pct']:.2f}% of {r['max_weekly_loss_pct']}%).")

        # --- 2. data quality, signal freshness, duplicates
        for msg in (quote_stale(q.time, now, r["max_quote_age_s"]),):
            if msg:
                why.append(msg)
        age = (now - _utc(p.time)).total_seconds()
        if age > r["max_signal_age_s"]:
            why.append(f"REJECTED: the signal is {age / 60:.0f} min old (limit {r['max_signal_age_s'] / 60:.0f} min).")
        if store.seen(p.signal_id) or (store.intent(f"fxs-{p.signal_id}") or {}).get("status") not in (None, "REJECTED", "CANCELLED"):
            why.append("REJECTED: duplicate signal (this entry was already handled).")
        if p.side not in (1, -1):
            why.append("REJECTED: the signal has no direction.")

        # --- 3. market conditions
        for msg in (market_closed_block(now, self.cfg), session_block(now, p.strategy, self.cfg),
                    rollover_block(now, p.style, self.cfg), news_block(ctx.events, p.symbol, now, self.cfg)):
            if msg:
                why.append(msg)
        session = current_session(now)
        day = trading_day_key(now, self.cfg["sessions"]["broker_tz"], self.cfg["sessions"]["rollover_hour"])
        if session and store.session_count(day, session) >= self.cfg["sessions"]["max_entries_per_session"]:
            why.append(f"REJECTED: the maximum entries for the {session} session ({self.cfg['sessions']['max_entries_per_session']}) is reached.")

        # --- 4. cooldown after a recent close
        cd = store.data["cooldowns"].get(f"{p.strategy}|{p.symbol}")
        if cd:
            mins = self.cfg["cooldown_min"].get(p.style, self.cfg["cooldown_min"]["default"])
            left = mins - (now - _utc(cd)).total_seconds() / 60
            if left > 0:
                why.append(f"REJECTED: cooldown after the last {p.strategy} trade on {p.symbol} ({left:.0f} min left).")

        # --- 5. stop, targets and reward-to-risk (against the price we would REALLY get)
        stop_dist = side * (exec_px - p.sl)
        if stop_dist <= 0:
            why.append("REJECTED: stop-loss distance is invalid (it is on the wrong side of the entry price).")
        elif stop_dist < spec.min_stop_distance:
            why.append("REJECTED: stop-loss distance is invalid (closer than the broker allows).")
        tps = sorted(p.tps, key=lambda x: side * x)
        if not tps or any(side * (t - exec_px) <= 0 for t in tps):
            why.append("REJECTED: take-profit is invalid (missing, or on the wrong side of the price).")
        if p.atr and stop_dist > 0:
            m = stop_dist / p.atr
            if m < r["stop_atr_min"]:
                why.append(f"REJECTED: stop is only {m:.2f} ATR away (needs {r['stop_atr_min']}+).")
            elif m > r["stop_atr_max"]:
                why.append(f"REJECTED: stop is {m:.1f} ATR away (max {r['stop_atr_max']}).")
        if stop_dist > 0 and tps and side * (tps[-1] - exec_px) > 0:
            rr = side * (tps[-1] - exec_px) / stop_dist
            d.checks["rr"] = round(rr, 2)
            if rr < r["min_rr"] - 1e-9:
                why.append(f"REJECTED: reward-to-risk {rr:.2f} is below the minimum {r['min_rr']}.")
        if stop_dist > 0 and abs(exec_px - p.entry) > r["chase_r"] * abs(p.entry - p.sl):
            why.append("REJECTED: price has already moved too far from the signal entry (not chasing).")

        # --- 6. spread and volatility
        spr = q.spread
        d.checks["spread_pips"] = round(spr / spec.pip_size, 2)
        if stop_dist > 0 and spr > r["max_spread_to_risk"] * stop_dist:
            why.append(f"REJECTED: spread {spr / spec.pip_size:.1f} pips is {100 * spr / stop_dist:.0f}% of the stop "
                       f"(max {100 * r['max_spread_to_risk']:.0f}%).")
        typical = spread_pips(self.main, p.symbol) * spec.pip_size
        if typical and spr > r["max_spread_vs_typical"] * typical:
            why.append(f"REJECTED: spread exceeds the configured maximum ({spr / spec.pip_size:.1f} pips vs typical "
                       f"{typical / spec.pip_size:.1f}).")
        if p.vol_ratio is not None:
            if p.vol_ratio > r["vol_ratio_max"]:
                why.append(f"REJECTED: volatility is {p.vol_ratio:.1f}x normal (max {r['vol_ratio_max']}x).")
            elif p.vol_ratio < r["vol_ratio_min"]:
                why.append(f"REJECTED: volatility is only {p.vol_ratio:.2f}x normal (dead market).")

        # --- 7. position size from the real equity
        size = None
        if stop_dist > 0:
            size = size_position(eq, r["risk_pct"], exec_px, p.sl, spec, r["slippage_pips"])
            d.checks["size"] = dict(volume=size.volume, risk_money=size.risk_money, target=size.risk_money_target,
                                    loss_per_lot=size.loss_per_lot, stop_pips=size.stop_pips)
            if not size.ok:
                why.append("REJECTED: " + size.reason[0].upper() + size.reason[1:] + ".")
            elif size.risk_money > eq * r["max_trade_risk_pct"] / 100 + 1e-9:
                why.append("REJECTED: trade would exceed the per-trade risk limit.")

        # --- 8. portfolio limits
        pos_n = len(ctx.positions) + len(ctx.pending)
        if pos_n >= r["max_positions"]:
            why.append(f"REJECTED: the maximum number of simultaneous positions ({r['max_positions']}) is reached.")
        same_symbol = [x for x in ctx.positions if x.symbol == p.symbol] + [o for o in ctx.pending if o.symbol == p.symbol]
        if len(same_symbol) >= r["max_positions_per_symbol"]:
            why.append(f"REJECTED: {p.symbol} already has {len(same_symbol)} position(s) or order(s) (max {r['max_positions_per_symbol']}).")
        strat_n = sum(1 for t in ctx.trades if t.symbol == p.symbol and t.strategy == p.strategy)
        if strat_n >= r["max_per_strategy_symbol"]:
            why.append(f"REJECTED: {p.strategy} already has a position on {p.symbol}.")
        if store.entries_today(day, p.symbol) >= r["max_entries_per_symbol_day"]:
            why.append(f"REJECTED: the maximum entries per day for {p.symbol} ({r['max_entries_per_symbol_day']}) is reached.")

        if size is not None and size.ok:
            open_risk = self.open_risk(ctx)
            d.checks["open_risk"] = round(open_risk, 2)
            cap = eq * r["max_total_risk_pct"] / 100
            if open_risk + size.risk_money > cap + 1e-9:
                why.append(f"REJECTED: trade would exceed account risk limits (open risk {open_risk:.2f} + {size.risk_money:.2f} "
                           f"> {cap:.2f}).")
            cur = self.currency_risk(ctx)
            ccap = eq * r["max_currency_risk_pct"] / 100
            for key in self._currency_dirs(p.symbol, side):
                if cur.get(key, 0.0) + size.risk_money > ccap + 1e-9:
                    why.append(f"REJECTED: existing exposure to {key[0]} ({'long' if key[1] > 0 else 'short'}) is too high.")
                    break
            for grp in r.get("correlation_groups", []):
                if p.symbol in grp:
                    for x in ctx.positions:
                        if x.symbol in grp and x.symbol != p.symbol and x.side == side:
                            why.append(f"REJECTED: correlated position already open ({x.symbol} in the same group as {p.symbol}).")
                            break

            # margin: if the broker cannot tell us, we refuse rather than guess
            try:
                need = ctx.broker.margin_required(p.symbol, size.volume, exec_px) if ctx.broker else None
            except BrokerError:
                need = None
            if need is None:
                why.append("REJECTED: the broker could not confirm the margin requirement.")
            else:
                d.checks["margin_needed"] = round(need, 2)
                if need * r["margin_buffer"] > ctx.account.margin_free:
                    why.append(f"REJECTED: insufficient available margin (needs {need:.2f} x {r['margin_buffer']}, "
                               f"free {ctx.account.margin_free:.2f}).")

        d.reasons = list(dict.fromkeys(why))   # keep order, drop repeats
        if not d.reasons and size is not None and size.ok:
            d.approved = True
            d.volume, d.risk_money = size.volume, size.risk_money
            d.exit_volumes = plan_exits(size.volume, len(tps), self.cfg["exits"]["tp_fractions"], spec)
        return d

    # ---------------- stop changes (used by the trade manager)
    def approve_stop_change(self, t: Trade, new_sl: float, price: float, spec: SymbolSpec, equity: float) -> tuple[bool, str]:
        """A stop may only move TOWARD the price (tighter). Widening is refused unless the trade explicitly allows it AND
        the resulting risk still fits the per-trade limit. The stop is never removed."""
        if new_sl is None:
            return False, "a stop-loss can never be removed"
        tighter = t.side * (new_sl - t.sl) > 0
        if t.side * (price - new_sl) < max(spec.min_stop_distance, spec.tick_size):
            return False, "the new stop is too close to the price or on the wrong side of it"
        if tighter:
            return True, "tighter"
        if not t.allow_widen:
            return False, "the initial stop is never widened automatically"
        risk = remaining_risk(t.side, t.volume_remaining, t.entry, new_sl, spec, self.cfg["risk"]["slippage_pips"])
        if risk > equity * self.cfg["risk"]["max_trade_risk_pct"] / 100:
            return False, "widening the stop would exceed the per-trade risk limit"
        return True, "widening allowed by the strategy and within the risk limit"
