"""Trade management from entry to final exit: TP1 / TP2 / TP3 partial closes, optional breakeven and trailing stops,
time / session / rollover / news exits, and safe handling when the broker refuses a stop change.

Rules that are never broken here:
  * the initial stop is never widened (only tighter moves, unless the trade explicitly allows widening AND the risk
    manager approves it);
  * a protective stop is never removed. If a modification fails the stop that exists stays, and if no stop exists any
    more it is restored or the position is closed;
  * a partial close can never close the whole position by accident (the last target alone does that);
  * swing trades are not cut by intraday rules.
"""
from __future__ import annotations

import pandas as pd

from .audit import AuditLog
from .filters import current_session, news_close_needed
from .models import BrokerError, Position, Quote, SymbolSpec
from .risk_manager import RiskManager
from .sizing import floor_to_step, remaining_risk
from .state import StateStore, Trade, transition
from .timeutil import minutes_to_rollover


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class TradeManager:
    def __init__(self, broker, risk: RiskManager, store: StateStore, audit: AuditLog, cfg: dict, notify=None, market=None):
        self.broker, self.risk, self.store, self.audit, self.cfg = broker, risk, store, audit, cfg
        self.notify = notify or (lambda text: None)
        self.market = market

    # ------------------------------------------------------------------ helpers
    def _say(self, text: str) -> None:
        try:
            self.notify(text)
        except Exception:
            pass   # an alert failing must never break trade management

    def _est_pnl(self, t: Trade, price: float, volume: float, spec: SymbolSpec) -> float:
        ticks = t.side * (price - t.entry) / spec.tick_size
        return ticks * spec.tick_value * volume - spec.commission_per_lot * volume / 2

    def refresh_risk(self, t: Trade, spec: SymbolSpec) -> None:
        t.remaining_risk = round(remaining_risk(t.side, t.volume_remaining, t.entry, t.sl, spec,
                                                self.cfg["risk"]["slippage_pips"]), 2)

    # ------------------------------------------------------------------ closing
    def finalize(self, t: Trade, now, reason: str | None = None, pnl_last: float | None = None, log_exit: bool = True) -> None:
        """The position is gone from the broker: record the final result."""
        ci = None
        try:
            ci = self.broker.closed_info(t.position_id)
        except BrokerError:
            pass
        if pnl_last is None:
            pnl_last = ci.pnl if ci else 0.0
        t.realized_pnl = round(t.realized_pnl + pnl_last, 2)
        t.closed_at = _utc(ci.close_time if ci else now).isoformat()
        t.close_reason = reason or (ci.reason if ci else "unknown")
        if not ci:
            t.notes.append("closed at the broker but no closing details were available: P&L may be incomplete")
        t.volume_remaining = 0.0
        t.remaining_risk = 0.0
        t.state = transition(t.state, "CLOSED")
        if log_exit:    # the broker closed it (stop, target, manual): the bot did not send the close itself
            self.audit.write("final_exit", now, trade_id=t.trade_id, reason=t.close_reason, by="broker")
        self.store.data["cooldowns"][f"{t.strategy}|{t.symbol}"] = _utc(now).isoformat()
        self.store.put_trade(t)
        self.audit.write("trade_closed", now, trade_id=t.trade_id, symbol=t.symbol, strategy=t.strategy,
                         reason=t.close_reason, realized_pnl=t.realized_pnl, notes=t.notes)
        self._say(f"CLOSED {t.symbol} {'BUY' if t.side > 0 else 'SELL'} ({t.strategy}): {t.close_reason}, "
                  f"realized P&L {t.realized_pnl:+.2f}")

    def close(self, t: Trade, pos: Position, volume: float | None, reason: str, now, spec: SymbolSpec, quote: Quote | None) -> bool:
        """Full close (volume None) or a partial close. Returns True when the broker confirmed it."""
        full = volume is None or volume >= pos.volume - 1e-12
        if full:
            t.state = transition(t.state, "CLOSING")
        try:
            res = self.broker.close_position(pos.position_id, None if full else volume)
        except BrokerError as e:
            self.audit.write("close_failed", now, trade_id=t.trade_id, reason=reason, error=str(e))
            if full:
                t.state = transition(t.state, "FILLED")
            self.store.put_trade(t)
            self._say(f"WARNING: could not close {t.symbol} ({reason}): {e}")
            return False
        if not res.ok:
            self.audit.write("close_failed", now, trade_id=t.trade_id, reason=reason, error=res.message)
            if full:
                t.state = transition(t.state, "FILLED")
            self.store.put_trade(t)
            self._say(f"WARNING: close of {t.symbol} was refused ({reason}): {res.message}")
            return False
        filled = res.filled_volume or (pos.volume if full else volume)
        price = res.fill_price if res.fill_price is not None else (quote.bid if t.side > 0 else quote.ask)
        est = self._est_pnl(t, price, filled, spec)
        if full or filled >= pos.volume - 1e-12:
            self.audit.write("final_exit", now, trade_id=t.trade_id, reason=reason, price=price, volume=filled)
            ci = None
            try:
                ci = self.broker.closed_info(t.position_id)
            except BrokerError:
                pass
            self.finalize(t, now, reason, pnl_last=(ci.pnl if ci else est), log_exit=False)
        else:
            t.volume_remaining = round(pos.volume - filled, 8)
            t.realized_pnl = round(t.realized_pnl + est, 2)
            self.refresh_risk(t, spec)
            self.store.put_trade(t)
            self.audit.write("partial_close", now, trade_id=t.trade_id, reason=reason, price=price, volume=filled,
                             remaining=t.volume_remaining, realized_pnl_est=round(est, 2))
            self._say(f"PARTIAL {t.symbol}: closed {filled} lots at {price} ({reason}), {t.volume_remaining} left")
        return True

    # ------------------------------------------------------------------ stops
    def set_stop(self, t: Trade, pos: Position, new_sl: float, why: str, now, spec: SymbolSpec, quote: Quote, equity: float) -> bool:
        price = quote.bid if t.side > 0 else quote.ask
        ok, reason = self.risk.approve_stop_change(t, new_sl, price, spec, equity)
        if not ok:
            self.audit.write("stop_change_refused", now, trade_id=t.trade_id, wanted=new_sl, why=reason)
            return False
        old = t.sl
        policy = self.cfg["exits"]["on_stop_failure"]
        attempts = 1 if policy == "alert_only" else 2
        err = ""
        for _ in range(attempts):
            try:
                self.broker.modify_position(pos.position_id, sl=new_sl)
                t.sl = new_sl
                t.stop_failures = 0
                self.refresh_risk(t, spec)
                self.store.put_trade(t)
                self.audit.write("stop_modified", now, trade_id=t.trade_id, old=old, new=new_sl, why=why,
                                 remaining_risk=t.remaining_risk)
                return True
            except BrokerError as e:
                err = str(e)
        # the change failed: report it and apply the safety policy
        t.stop_failures += 1
        self.audit.write("stop_modify_failed", now, trade_id=t.trade_id, wanted=new_sl, error=err, policy=policy)
        self._say(f"WARNING: stop change on {t.symbol} failed ({err}). Policy: {policy}")
        self.ensure_protected(t, pos.position_id, now, spec, quote, force_check=True)
        if policy == "close" and t.state != "CLOSED":
            cur = self._position(pos.position_id)
            if cur:
                self.close(t, cur, None, "stop modification failed (policy: close)", now, spec, quote)
        self.store.put_trade(t)
        return False

    def _position(self, position_id: str) -> Position | None:
        try:
            return next((p for p in self.broker.positions() if p.position_id == position_id), None)
        except BrokerError:
            return None

    def ensure_protected(self, t: Trade, position_id: str, now, spec: SymbolSpec, quote: Quote | None, force_check: bool = False) -> None:
        """A managed position must always have a stop. If it has none (broker glitch, failed modification), put the last
        known stop back; if that also fails, get out of the position."""
        pos = self._position(position_id)
        if pos is None or pos.sl is not None or not t.managed:
            return
        self.audit.write("stop_missing", now, trade_id=t.trade_id, last_known=t.sl)
        self._say(f"ALERT: {t.symbol} has NO protective stop. Restoring {t.sl}.")
        for _ in range(2):
            try:
                self.broker.modify_position(position_id, sl=t.sl)
                self.audit.write("stop_restored", now, trade_id=t.trade_id, sl=t.sl)
                return
            except BrokerError as e:
                last = str(e)
        self.audit.write("stop_restore_failed", now, trade_id=t.trade_id, error=last)
        self._say(f"ALERT: could not restore the stop on {t.symbol}. Closing the position for safety.")
        pos = self._position(position_id)
        if pos:
            self.close(t, pos, None, "protective stop could not be restored", now, spec, quote)

    # ------------------------------------------------------------------ the per-tick management
    def manage(self, t: Trade, pos: Position, quote: Quote, spec: SymbolSpec, equity: float, now, events=None) -> None:
        if not t.managed:
            return
        cfg = self.cfg
        now = _utc(now)
        exit_px = quote.bid if t.side > 0 else quote.ask

        # the broker's stop is the truth: if it differs from ours (changed by hand), follow the broker
        if pos.sl is not None and abs(pos.sl - t.sl) > spec.tick_size / 2:
            t.notes.append(f"stop changed outside the bot: {t.sl} -> {pos.sl}")
            self.audit.write("stop_changed_externally", now, trade_id=t.trade_id, ours=t.sl, broker=pos.sl)
            t.sl = pos.sl
        if abs(pos.volume - t.volume_remaining) > spec.volume_step / 2:
            t.notes.append(f"volume changed outside the bot: {t.volume_remaining} -> {pos.volume}")
            self.audit.write("volume_changed_externally", now, trade_id=t.trade_id, ours=t.volume_remaining, broker=pos.volume)
            t.volume_remaining = pos.volume
        self.refresh_risk(t, spec)

        # ---- exits that close everything
        why = self._time_exit(t, now, events)
        if why:
            self.close(t, pos, None, why, now, spec, quote)
            return

        # ---- targets
        n = len(t.tps)
        while t.tp_done < n:
            i = t.tp_done
            hit = t.side * (exit_px - t.tps[i]) >= 0
            if not hit:
                break
            if i == n - 1:  # the last target closes whatever is left
                if self.close(t, pos, None, f"TP{i + 1} reached", now, spec, quote):
                    t.tp_done = n
                return
            vol = t.exit_volumes[i]
            if not vol:
                t.tp_done += 1
                t.notes.append(f"TP{i + 1} reached: partial skipped (volume too small for the broker's minimum)")
                self.audit.write("partial_skipped", now, trade_id=t.trade_id, target=i + 1)
                continue
            vol = min(vol, floor_to_step(pos.volume - spec.volume_min, spec.volume_step))   # never close it all by accident
            if vol < spec.volume_min - 1e-12:
                t.tp_done += 1
                t.notes.append(f"TP{i + 1} reached: partial skipped (it would leave less than the minimum volume)")
                continue
            if not self.close(t, pos, vol, f"TP{i + 1} reached", now, spec, quote):
                break    # retry next tick
            t.tp_done += 1
            self.store.put_trade(t)
            pos = self._position(pos.position_id) or pos
            if t.state == "CLOSED":
                return

        self._breakeven(t, pos, quote, spec, equity, now)
        self._trail(t, pos, quote, spec, equity, now)
        self.store.put_trade(t)

    def _breakeven(self, t, pos, quote, spec, equity, now) -> None:
        be = self.cfg["exits"]["breakeven"]
        if not be["enabled"] or t.be_done:
            return
        need = {"tp1": 1, "tp2": 2}.get(be["after"], 1)
        if t.tp_done < need or t.tp_done >= len(t.tps):
            return
        per_lot_commission = spec.commission_per_lot / (spec.tick_value / spec.tick_size) if spec.tick_value else 0.0
        offset = quote.spread + 2 * self.cfg["risk"]["slippage_pips"] * spec.pip_size + per_lot_commission
        new_sl = t.entry + t.side * offset     # covers spread, expected slippage and commission, not just the entry price
        if t.side * (new_sl - t.sl) <= 0:
            t.be_done = True
            return
        if self.set_stop(t, pos, new_sl, "breakeven after TP%d (costs covered)" % need, now, spec, quote, equity):
            t.be_done = True

    def _trail(self, t, pos, quote, spec, equity, now) -> None:
        tr = self.cfg["exits"]["trailing"]
        mode = tr["mode"]
        if mode == "none" or self.market is None:
            return
        need = {"tp1": 1, "tp2": 2, "entry": 0}.get(tr["start_after"], 1)
        if t.tp_done < need:
            return
        price = quote.bid if t.side > 0 else quote.ask
        cand = None
        if mode == "atr":
            a = self.market.atr(t.symbol) or t.atr
            if a:
                cand = price - t.side * tr["atr_mult"] * a
        elif mode == "swing":
            lvl = self.market.swing(t.symbol, t.side)
            if lvl is not None:
                cand = lvl - t.side * spec.pip_size
        elif mode == "strategy":
            cand = self.market.strategy_trail(t)
        if cand is None or t.side * (cand - t.sl) < spec.pip_size:   # only tighter, and by at least a pip
            return
        self.set_stop(t, pos, round(cand, spec.digits), f"{mode} trailing stop", now, spec, quote, equity)

    def _time_exit(self, t: Trade, now: pd.Timestamp, events) -> str | None:
        cfg = self.cfg
        sc = cfg["sessions"]
        held_h = (now - _utc(t.opened_at)).total_seconds() / 3600
        limit = cfg["exits"]["max_hold_hours"].get(t.style)
        if limit is not None and held_h >= limit:
            return f"maximum holding time for {t.style} trades reached ({limit} h)"
        if t.style in sc["close_before_rollover"] or t.style not in sc["allow_overnight"]:
            if minutes_to_rollover(now, sc["broker_tz"], sc["rollover_hour"]) <= sc["close_before_rollover_min"]:
                return "closing before rollover"
        if t.style not in sc["hold_outside_session"]:
            allowed = sc.get("strategy_sessions", {}).get(t.strategy, sc.get("allowed"))
            if allowed:
                from .timeutil import active_sessions
                active = active_sessions(now)
                if not any(a in active for a in allowed):
                    return "the trading session has ended"
        n = news_close_needed(events, t.symbol, now, cfg)
        if n:
            return "closing ahead of news: " + n.replace("REJECTED: ", "")
        return None
