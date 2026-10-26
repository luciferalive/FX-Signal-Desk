"""The trading engine. One tick = reconcile with the broker, manage open trades, then (only if allowed) look for entries:

    candidates -> arbitration -> RISK MANAGER (the only approver) -> order manager -> broker

Safety rules enforced here:
  * demo profile never runs on a live account and the live profile never runs on a demo account;
  * live trading needs `armed_live=True`, which only a human-typed confirmation sets, per process, never from a file;
    an unarmed live engine only watches;
  * an order is written to the saved state BEFORE it is sent, and a timeout is never retried blindly: the broker is asked
    what happened first (by the client order id);
  * on every start the broker is reconciled and trading resumes only if that succeeds;
  * a persistent risk lock stops entries when the daily / weekly loss limit is hit.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from dataclasses import asdict

import pandas as pd

from ..config import load as load_main_config
from .arbitration import arbitrate
from .audit import AuditLog, scrub
from .exits import plan_exits
from .filters import current_session
from .models import (BrokerConnectionError, BrokerError, BrokerTimeout, OrderRequest, Position, SymbolSpec)
from .risk_manager import Context, Decision, Proposal, RiskManager, loss_status, update_baselines
from .state import CommandQueue, StateStore, Trade, transition
from .timeutil import trading_day_key, trading_week_key
from .trade_manager import TradeManager

PREFIX = "fxs-"


class TradingSafetyError(Exception):
    """The configuration and the connected account do not match, or live trading was not deliberately armed."""


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class TradingEngine:
    def __init__(self, broker, cfg: dict, store: StateStore, audit: AuditLog, commands: CommandQueue | None = None,
                 signals=None, events=None, market=None, clock=None, notify=None, armed_live: bool = False,
                 main_cfg: dict | None = None, status_path: str | None = None):
        self.broker, self.cfg, self.store, self.audit = broker, cfg, store, audit
        self.commands = commands or CommandQueue(None)
        self.signals = signals or (lambda now: [])
        self.events = events or (lambda: [])
        self.clock = clock
        self.notify = notify or (lambda text: None)
        self.armed_live = armed_live
        self.status_path = status_path
        self.risk = RiskManager(cfg, main_cfg or load_main_config())
        self.mgr = TradeManager(broker, self.risk, store, audit, cfg, self._say, market)
        self.ready = False
        self.monitor_only = False
        self.connected = False
        self.last_error = ""
        self.simulated = getattr(broker, "name", "") == "sim"
        self.account_is_live: bool | None = None
        self.recent: deque = deque(maxlen=25)
        self._logged: set = set()

    # ------------------------------------------------------------------ small helpers
    def _now(self) -> pd.Timestamp:
        return _utc(self.clock() if self.clock else pd.Timestamp.now(tz="UTC"))

    def _say(self, text: str) -> None:
        try:
            self.notify(text)
        except Exception:
            pass

    @property
    def mode_label(self) -> str:
        if not self.connected:
            return "DISCONNECTED"
        return "LIVE ACCOUNT" if self.account_is_live else "DEMO ACCOUNT"

    # ------------------------------------------------------------------ start and reconcile
    def start(self) -> bool:
        """Connect, check the account matches the profile, reconcile. Returns True when trading logic may run."""
        now = self._now()
        self.audit.write("engine_start", now, profile=self.cfg["profile"], broker=getattr(self.broker, "name", "?"))
        try:
            self.broker.connect()
            acct = self.broker.account()
        except BrokerError as e:
            self.connected, self.ready, self.last_error = False, False, f"cannot connect: {e}"
            self.audit.write("connect_failed", now, error=str(e))
            return False
        self.connected = True
        self.account_is_live = acct.is_live
        profile = self.cfg["profile"]
        if profile == "demo" and acct.is_live:
            self.ready = False
            self.last_error = "the DEMO profile is selected but the broker account is LIVE: refusing to trade"
            self.audit.write("safety_refusal", now, why=self.last_error)
            raise TradingSafetyError(self.last_error)
        if profile == "live" and not acct.is_live:
            self.ready = False
            self.last_error = "the LIVE profile is selected but the broker account is a demo account: refusing, use the demo profile"
            self.audit.write("safety_refusal", now, why=self.last_error)
            raise TradingSafetyError(self.last_error)
        self.monitor_only = profile == "live" and not self.armed_live
        if self.monitor_only:
            self.audit.write("live_not_armed", now, note="LIVE profile without confirmation: watching only, no orders")
        try:
            report = self.reconcile()
        except BrokerError as e:
            self.ready, self.last_error = False, f"reconciliation failed: {e}"
            self.audit.write("reconcile_failed", now, error=str(e))
            return False
        update_baselines(self.store, acct.equity, now, self.cfg)
        self.store.save()
        self.ready, self.last_error = True, ""
        self.audit.write("reconciled", now, **report)
        return True

    def reconcile(self) -> dict:
        """Make the saved state agree with what the broker really holds. Never creates a trade, only records reality."""
        now = self._now()
        positions = self.broker.positions()
        self.broker.pending_orders()   # a failure here must stop the start-up too
        by_id = {p.position_id: p for p in positions}
        rep = dict(closed=[], recovered=[], adopted=[], resolved=[])
        for t in self.store.trades(open_only=True):
            if t.position_id not in by_id:
                self.mgr.finalize(t, now)
                rep["closed"].append(t.trade_id)
        self._adopt_unknown(positions, now, rep)
        for cid, it in list(self.store.data["intents"].items()):
            if it.get("status") in ("SUBMITTED", "ERROR") and it.get("unconfirmed", it.get("status") == "SUBMITTED"):
                rep["resolved"].append((cid, self._resolve_intent(cid, now)))
        self.store.save()
        return rep

    def _adopt_unknown(self, positions, now, rep: dict | None = None) -> None:
        """Positions at the broker that have no trade record: ours (rebuilt from the saved order) or manual (counted in
        risk and shown, never touched)."""
        known = {t.position_id for t in self.store.trades()}
        for p in positions:
            if p.position_id in known:
                continue
            intent = self.store.intent(p.comment) if p.comment.startswith(PREFIX) else None
            spec = self.broker.symbol_spec(p.symbol)
            if intent and intent.get("plan"):
                t = self._trade_from_intent(p.comment, intent, p, spec, now)
                if rep is not None:
                    rep["recovered"].append(t.trade_id)
                self.audit.write("trade_recovered", now, trade_id=t.trade_id, position_id=p.position_id)
            else:
                t = Trade(f"ext-{p.position_id}", p.position_id, p.comment, p.symbol, p.side, "manual", "", "manual",
                          p.open_price, p.open_price, p.sl or 0.0, p.sl or 0.0, [], [], p.volume, p.volume,
                          _utc(p.open_time).isoformat(), managed=False,
                          notes=["position found at the broker that this bot did not open: counted in risk, never modified"])
                if rep is not None:
                    rep["adopted"].append(t.trade_id)
                self.audit.write("position_adopted", now, trade_id=t.trade_id, symbol=p.symbol, volume=p.volume)
            self.store.put_trade(t)

    def _trade_from_intent(self, cid: str, it: dict, p: Position, spec: SymbolSpec, now) -> Trade:
        plan = it["plan"]
        orig = float(it.get("filled_volume") or max(plan["volume"], p.volume))
        orig = max(orig, p.volume)
        closed = orig - p.volume
        tp_done, cum = 0, 0.0
        for v in plan["exit_volumes"][:-1]:
            if v and cum + v <= closed + spec.volume_step / 2:
                cum += v
                tp_done += 1
            elif not v:
                tp_done += 1 if tp_done == len([x for x in plan["exit_volumes"][:tp_done + 1]]) - 1 else 0
            else:
                break
        t = Trade(cid, p.position_id, cid, p.symbol, p.side, it["strategy"], it.get("family", ""), it.get("style", "intraday"),
                  float(it["signal_entry"]), p.open_price, float(plan["sl"]), float(p.sl if p.sl is not None else plan["sl"]),
                  [float(x) for x in plan["tps"]], plan["exit_volumes"], round(orig, 8), p.volume,
                  _utc(p.open_time).isoformat(), tp_done=tp_done, atr=it.get("atr"), allow_widen=bool(it.get("allow_widen")),
                  notes=["recovered after a restart from the saved order and the broker position"])
        t.realized_pnl = float(it.get("realized_pnl", 0.0))
        self.mgr.refresh_risk(t, spec)
        self.store.data["intents"][cid]["status"] = transition(it.get("status"), "FILLED") if it.get("status") != "FILLED" else "FILLED"
        self.store.data["intents"][cid]["unconfirmed"] = False
        return t

    def _resolve_intent(self, cid: str, now) -> str:
        """An order whose outcome we do not know (timeout, crash). Ask the broker; never send it again."""
        it = self.store.intent(cid)
        try:
            found = self.broker.find_by_client_id(cid)
        except BrokerError as e:
            self.audit.write("intent_unresolved", now, client_id=cid, error=str(e))
            return "unresolved"
        if found and found[0] == "position":
            pos = found[1]
            spec = self.broker.symbol_spec(pos.symbol)
            if it.get("plan") and not any(t.position_id == pos.position_id for t in self.store.trades()):
                it["filled_volume"] = pos.volume
                t = self._trade_from_intent(cid, it, pos, spec, now)
                self.store.put_trade(t)
                self.store.count_entry(trading_day_key(now, self.cfg["sessions"]["broker_tz"], self.cfg["sessions"]["rollover_hour"]),
                                       t.symbol, current_session(now))
            self.audit.write("intent_resolved", now, client_id=cid, outcome="filled")
            return "filled"
        if found and found[0] == "closed":
            it["status"] = "FILLED"
            it["unconfirmed"] = False
            self.audit.write("intent_resolved", now, client_id=cid, outcome="filled and already closed")
            return "closed"
        if found and found[0] == "order":
            it["unconfirmed"] = False
            it["status"] = "SUBMITTED"
            self.audit.write("intent_resolved", now, client_id=cid, outcome="pending order at the broker")
            return "pending"
        age = (now - _utc(it.get("submitted_at", now))).total_seconds()
        if age >= self.cfg["execution"]["unconfirmed_grace_s"]:
            it["status"] = transition(it.get("status"), "CANCELLED")
            it["unconfirmed"] = False
            self.audit.write("intent_resolved", now, client_id=cid, outcome="not at the broker: assumed not executed")
            return "cancelled"
        return "waiting"

    # ------------------------------------------------------------------ the tick
    def tick(self) -> dict:
        now = self._now()
        out: dict = {"time": now.isoformat(), "entries": [], "note": ""}
        self._process_commands(now)
        if not (self.connected and self.ready):
            if not self._recover(now):
                self.write_status(now)
                out["note"] = self.last_error
                return out
        try:
            acct = self.broker.account()
            positions = self.broker.positions()
            pending = self.broker.pending_orders()
            specs = {s: self.broker.symbol_spec(s) for s in {p.symbol for p in positions} | {o.symbol for o in pending}}
        except BrokerConnectionError as e:
            self.connected = self.ready = False
            self.last_error = f"connection lost: {e}"
            self.audit.write("connection_lost", now, error=str(e))
            self._say("WARNING: lost the broker connection. Protective stops stay at the broker. Reconnecting automatically.")
            self.write_status(now)
            out["note"] = self.last_error
            return out
        except BrokerError as e:
            self.last_error = f"broker error: {e}"
            self.audit.write("tick_error", now, error=str(e))
            self.write_status(now)
            return out

        update_baselines(self.store, acct.equity, now, self.cfg)
        self._check_locks(acct, positions, pending, specs, now)

        self._adopt_unknown(positions, now)
        for cid, it in list(self.store.data["intents"].items()):     # orders whose outcome was unknown: ask the broker again
            if it.get("status") == "ERROR" and it.get("unconfirmed"):
                self._resolve_intent(cid, now)
        by_id = {p.position_id: p for p in positions}
        for t in self.store.trades(open_only=True):          # trades that ended at the broker (stop, target, manual)
            if t.position_id not in by_id:
                self.mgr.finalize(t, now)
        events = self.events() or []

        if not self.monitor_only:
            for t in self.store.trades(open_only=True):
                pos = by_id.get(t.position_id)
                if pos is None or not t.managed:
                    continue
                try:
                    quote = self.broker.quote(t.symbol)
                    spec = specs.get(t.symbol) or self.broker.symbol_spec(t.symbol)
                    if pos.sl is None:
                        self.mgr.ensure_protected(t, pos.position_id, now, spec, quote)
                        continue
                    if not self.store.data["stopped"]:       # STOP ALL = hands off, the broker's stops keep protecting
                        self.mgr.manage(t, pos, quote, spec, acct.equity, now, events)
                except BrokerError as e:
                    self.audit.write("manage_error", now, trade_id=t.trade_id, error=str(e))

        blocked = self._entries_blocked()
        if blocked:
            out["note"] = blocked
        else:
            out["entries"] = self._look_for_entries(now, events)
        self.store.save()
        self.write_status(now)
        return out

    def _recover(self, now) -> bool:
        """After a lost connection or failed start: reconnect and reconcile before anything else happens."""
        try:
            self.broker.connect()
            self.connected = True
            acct = self.broker.account()
            self.account_is_live = acct.is_live
            rep = self.reconcile()
            self.ready, self.last_error = True, ""
            self.audit.write("recovered", now, **rep)
            self._say("Reconnected to the broker and reconciled the open trades.")
            return True
        except BrokerError as e:
            self.connected, self.ready, self.last_error = False, False, f"still disconnected: {e}"
            return False

    def _entries_blocked(self) -> str | None:
        if self.monitor_only:
            return "LIVE trading is not armed: watching only"
        if self.store.data["stopped"]:
            return "automated trading is stopped"
        if self.store.data["paused"]:
            return "new entries are paused"
        if self.store.data.get("lock"):
            return "risk lock active: " + self.store.data["lock"]["reason"]
        return None

    # ------------------------------------------------------------------ risk lock
    def _check_locks(self, acct, positions, pending, specs, now) -> None:
        sc = self.cfg["sessions"]
        lock = self.store.data.get("lock")
        day = trading_day_key(now, sc["broker_tz"], sc["rollover_hour"])
        week = trading_week_key(now, sc["broker_tz"], sc["rollover_hour"])
        if lock and ((lock["kind"] == "daily" and lock["key"] != day) or (lock["kind"] == "weekly" and lock["key"] != week)):
            self.audit.write("lock_released", now, kind=lock["kind"], why="a new trading " + ("day" if lock["kind"] == "daily" else "week") + " started")
            self.store.data["lock"] = lock = None
        st = loss_status(self.store, acct.equity, self.cfg)
        if lock or not (st["day_limit_hit"] or st["week_limit_hit"]) or self.monitor_only:
            return
        kind = "weekly" if st["week_limit_hit"] else "daily"
        reason = (f"weekly loss limit reached ({st['week_loss_pct']:.2f}%)" if kind == "weekly"
                  else f"daily loss limit reached ({st['day_loss_pct']:.2f}%)")
        self.store.data["lock"] = dict(kind=kind, reason=reason, since=now.isoformat(), key=week if kind == "weekly" else day)
        self.audit.write("risk_lock", now, kind=kind, reason=reason, loss=st)
        self._cancel_entry_orders(pending, now)
        policy = self.cfg["daily_lock"]["existing_positions"]
        closed = 0
        if policy != "keep":
            for p in positions:
                if policy == "close_all" or p.unrealized_pnl < 0:
                    t = next((x for x in self.store.trades(open_only=True) if x.position_id == p.position_id), None)
                    try:
                        if t:
                            closed += bool(self.mgr.close(t, p, None, f"risk lock policy: {policy}", now, specs[p.symbol], None))
                        else:
                            closed += bool(self.broker.close_position(p.position_id).ok)
                    except BrokerError as e:
                        self.audit.write("lock_close_failed", now, position=p.position_id, error=str(e))
        self._say(f"RISK LOCK: {reason}. New entries are blocked, pending entry orders cancelled. "
                  f"Existing positions: policy '{policy}' ({closed} closed). Open positions stay monitored.")
        self.store.save()

    def _cancel_entry_orders(self, pending, now) -> int:
        n = 0
        for o in pending:
            if o.comment.startswith(PREFIX):
                try:
                    self.broker.cancel_order(o.order_id)
                    it = self.store.intent(o.comment)
                    if it:
                        it["status"] = transition(it.get("status"), "CANCELLED")
                    self.audit.write("order_cancelled", now, order_id=o.order_id, client_id=o.comment)
                    n += 1
                except BrokerError as e:
                    self.audit.write("cancel_failed", now, order_id=o.order_id, error=str(e))
        return n

    # ------------------------------------------------------------------ entries
    def _context(self, symbol: str, now, events) -> Context:
        acct = self.broker.account()
        positions = self.broker.positions()
        pending = self.broker.pending_orders()
        specs = {s: self.broker.symbol_spec(s) for s in {p.symbol for p in positions} | {o.symbol for o in pending} | {symbol}}
        return Context(acct, specs[symbol], self.broker.quote(symbol), positions, pending, self.store.trades(open_only=True),
                       specs, self.store, now, events, self.broker)

    def _look_for_entries(self, now, events) -> list[dict]:
        try:
            cands = [c for c in (self.signals(now) or []) if c is not None]
        except Exception as e:     # a strategy bug must never stop trade management
            self.audit.write("signal_error", now, error=f"{type(e).__name__}: {e}")
            return []
        if not cands:
            return []
        props, notes = arbitrate(cands, self.cfg)
        for n in notes:
            key = ("arb", n["symbol"], n["action"], n["why"])
            if key not in self._logged:
                self._logged.add(key)
                self.audit.write("arbitration", now, **n)
        results = []
        for p in props:
            if any((it.get("symbol") == p.symbol and it.get("status") == "ERROR" and it.get("unconfirmed"))
                   for it in self.store.data["intents"].values()):
                self._log_rejection(p, ["REJECTED: an earlier order on this symbol is unconfirmed; waiting for the broker."], now, None)
                continue
            try:
                ctx = self._context(p.symbol, now, events)
                dec = self.risk.evaluate(p, ctx)
            except BrokerError as e:
                self._log_rejection(p, [f"REJECTED: broker data unavailable ({e})"], now, None)
                continue
            if not dec.approved:
                self._log_rejection(p, dec.reasons, now, (ctx, dec))
                results.append(dict(symbol=p.symbol, approved=False, reasons=dec.reasons))
                continue
            trade = self._execute(p, dec, ctx, now)
            results.append(dict(symbol=p.symbol, approved=True, opened=bool(trade)))
        return results

    def _decision_record(self, p: Proposal, ctx: Context | None, dec: Decision | None) -> dict:
        rec = dict(signal=asdict(p))
        if ctx:
            q, a = ctx.quote, ctx.account
            rec["market"] = dict(bid=q.bid, ask=q.ask, spread=round(q.spread, 6), quote_time=_utc(q.time).isoformat(),
                                 session=current_session(ctx.now))
            rec["account"] = dict(balance=a.balance, equity=a.equity, margin_used=a.margin_used, margin_free=a.margin_free,
                                  margin_level=a.margin_level, open_positions=len(ctx.positions), pending=len(ctx.pending))
        if dec:
            rec["risk"] = dec.checks
            rec["decision"] = "APPROVED" if dec.approved else "REJECTED"
            rec["reasons"] = dec.reasons
            rec["volume"], rec["risk_money"] = dec.volume, dec.risk_money
        return rec

    def _log_rejection(self, p: Proposal, reasons: list[str], now, pair) -> None:
        key = ("rej", p.signal_id, tuple(reasons))
        if key in self._logged:
            return
        self._logged.add(key)
        rec = self._decision_record(p, *(pair or (None, None)))
        rec.setdefault("decision", "REJECTED")
        rec["reasons"] = reasons
        self.audit.write("decision", now, **rec)
        self.recent.append(dict(time=now.isoformat(), symbol=p.symbol, side="BUY" if p.side > 0 else "SELL",
                                strategy=p.strategy, decision="REJECTED", reasons=reasons))
        if len(self._logged) > 5000:
            self._logged.clear()

    def _execute(self, p: Proposal, dec: Decision, ctx: Context, now) -> Trade | None:
        """The order manager: write the intent first, send, then confirm from the broker's answer."""
        cid = f"{PREFIX}{p.signal_id}"
        tps = sorted(p.tps, key=lambda x: p.side * x)
        plan = dict(sl=p.sl, tps=tps, exit_volumes=dec.exit_volumes, volume=dec.volume)
        st = "CREATED"
        st = transition(st, "VALIDATING")
        st = transition(st, "APPROVED")
        self.store.put_intent(cid, status=st, symbol=p.symbol, side=p.side, strategy=p.strategy, family=p.family, style=p.style,
                              signal_entry=p.entry, atr=p.atr, allow_widen=p.allow_widen, plan=plan, unconfirmed=False,
                              submitted_at=now.isoformat(), explanation=p.explanation)
        self.store.mark_seen(p.signal_id)
        self.audit.write("decision", now, **self._decision_record(p, ctx, dec))
        req = OrderRequest(cid, p.symbol, p.side, dec.volume, "market", sl=p.sl, tp=tps[-1])
        it = self.store.intent(cid)
        it["status"] = transition(it["status"], "SUBMITTED")
        self.store.save()                      # saved BEFORE sending: a crash from here on can be reconciled
        self.audit.write("order_request", now, client_id=cid, symbol=p.symbol, side=p.side, volume=dec.volume,
                         sl=p.sl, tp=tps[-1], expected_price=dec.exec_price)
        try:
            res = self.broker.place_order(req)
        except (BrokerTimeout, BrokerConnectionError) as e:
            it["status"], it["unconfirmed"] = "ERROR", True      # unknown outcome: reconcile, NEVER retry blindly
            self.audit.write("order_unconfirmed", now, client_id=cid, error=str(e))
            self._say(f"WARNING: order for {p.symbol} timed out; checking the broker instead of re-sending.")
            outcome = self._resolve_intent(cid, now)
            self.store.save()
            return next((t for t in self.store.trades(open_only=True) if t.client_id == cid), None) if outcome == "filled" else None
        except BrokerError as e:
            it["status"] = transition(it["status"], "ERROR")
            it["unconfirmed"] = False
            self.audit.write("order_error", now, client_id=cid, error=str(e))
            self.store.save()
            return None
        self.audit.write("broker_response", now, client_id=cid, ok=res.ok, status=res.status, order_id=res.order_id,
                         position_id=res.position_id, fill_price=res.fill_price, filled_volume=res.filled_volume, message=res.message)
        if not res.ok:
            it["status"] = transition(it["status"], "REJECTED")
            self.store.save()
            self.recent.append(dict(time=now.isoformat(), symbol=p.symbol, decision="BROKER REJECTED", reasons=[res.message]))
            return None
        if res.status != "filled" or not res.position_id:
            it["status"] = "SUBMITTED"                         # accepted but not filled (pending order)
            self.store.save()
            return None
        it["filled_volume"] = res.filled_volume
        it["status"] = transition(it["status"], "FILLED" if res.filled_volume >= dec.volume - 1e-12 else "PARTIALLY_FILLED")
        spec = ctx.spec
        exit_volumes = dec.exit_volumes if res.filled_volume >= dec.volume - 1e-12 else plan_exits(
            res.filled_volume, len(tps), self.cfg["exits"]["tp_fractions"], spec)
        entry = res.fill_price if res.fill_price is not None else dec.exec_price
        t = Trade(cid, res.position_id, cid, p.symbol, p.side, p.strategy, p.family, p.style, p.entry, entry, p.sl, p.sl, tps,
                  exit_volumes, round(res.filled_volume, 8), round(res.filled_volume, 8), now.isoformat(),
                  atr=p.atr, allow_widen=p.allow_widen,
                  notes=([f"partial fill: {res.filled_volume} of {dec.volume} lots, the rest was not filled"]
                         if res.filled_volume < dec.volume - 1e-12 else []))
        self.mgr.refresh_risk(t, spec)
        self.store.put_trade(t)
        sess = current_session(now)
        self.store.count_entry(trading_day_key(now, self.cfg["sessions"]["broker_tz"], self.cfg["sessions"]["rollover_hour"]), p.symbol, sess)
        self.audit.write("execution", now, trade_id=cid, symbol=p.symbol, side=p.side, strategy=p.strategy, price=entry,
                         volume=res.filled_volume, sl=p.sl, tps=tps, risk_money=dec.risk_money, sources=p.sources)
        self.recent.append(dict(time=now.isoformat(), symbol=p.symbol, side="BUY" if p.side > 0 else "SELL", strategy=p.strategy,
                                decision="OPENED", reasons=[]))
        self.store.save()
        self.mgr.ensure_protected(t, res.position_id, now, spec, ctx.quote)    # the stop must really be there
        self._say(f"OPENED {'BUY' if p.side > 0 else 'SELL'} {p.symbol} {res.filled_volume} lots @ {entry} | SL {p.sl} | "
                  f"TP {' / '.join(str(x) for x in tps)} | risk {dec.risk_money:.2f} | {p.strategy}")
        return t

    # ------------------------------------------------------------------ operator commands
    def _process_commands(self, now) -> None:
        for c in self.commands.drain():
            a, args = c.get("action"), c.get("args", {})
            try:
                msg = self._do_command(a, args, now)
            except Exception as e:
                msg = f"failed: {type(e).__name__}: {e}"
            self.audit.write("command", now, action=a, args=args, result=msg)

    def _do_command(self, a: str, args: dict, now) -> str:
        destructive = a in ("close", "close_all", "liquidate")
        if destructive and self.cfg["profile"] == "live" and not args.get("confirm"):
            return "refused: live trading needs explicit confirmation for this action"
        if a == "pause":
            self.store.data["paused"] = True
            return "new entries paused"
        if a == "resume":
            self.store.data["paused"] = False
            return "new entries resumed"
        if a == "stop":
            self.store.data["stopped"] = True
            n = self._cancel_entry_orders(self.broker.pending_orders(), now) if self.connected else 0
            return f"STOP ALL TRADING: no new signals, {n} pending entry order(s) cancelled, protective stops keep working"
        if a == "start":
            self.store.data["stopped"] = False
            return "automated trading re-enabled"
        if a == "unlock":
            self.store.data["lock"] = None
            return "risk lock cleared by the operator (it re-locks at once if the loss limit is still exceeded)"
        if not self.connected:
            return "refused: not connected to the broker"
        positions = self.broker.positions()
        if a == "close":
            pid = args.get("position_id")
            p = next((x for x in positions if x.position_id == pid), None)
            if not p:
                return f"position {pid} not found"
            return self._close_one(p, "closed by the operator", now)
        if a in ("close_all", "liquidate"):
            if a == "liquidate":
                self._cancel_entry_orders(self.broker.pending_orders(), now)
                for o in self.broker.pending_orders():
                    try:
                        self.broker.cancel_order(o.order_id)
                    except BrokerError:
                        pass
                self.store.data["stopped"] = True
            res = [self._close_one(p, f"{a} by the operator", now) for p in positions]
            return f"{a}: {len(positions)} position(s): " + "; ".join(res)
        return f"unknown command {a}"

    def _close_one(self, p: Position, reason: str, now) -> str:
        t = next((x for x in self.store.trades(open_only=True) if x.position_id == p.position_id), None)
        spec = self.broker.symbol_spec(p.symbol)
        if t:
            ok = self.mgr.close(t, p, None, reason, now, spec, self.broker.quote(p.symbol))
            return f"{p.symbol} {'closed' if ok else 'close FAILED'}"
        r = self.broker.close_position(p.position_id)
        return f"{p.symbol} {'closed' if r.ok else 'close FAILED: ' + r.message}"

    # ------------------------------------------------------------------ status for the panel
    def status(self, now=None) -> dict:
        now = _utc(now or self._now())
        acct = None
        rows = []
        pending_n = 0
        try:
            if self.connected:
                acct = self.broker.account()
                positions = {p.position_id: p for p in self.broker.positions()}
                pending_n = len(self.broker.pending_orders())
                for t in self.store.trades(open_only=True):
                    p = positions.get(t.position_id)
                    if not p:
                        continue
                    try:
                        q = self.broker.quote(t.symbol)
                        cur = q.bid if t.side > 0 else q.ask
                    except BrokerError:
                        cur = None
                    tps = (t.tps + [None, None, None])[:3]
                    rows.append(dict(trade_id=t.trade_id, position_id=t.position_id, symbol=t.symbol,
                                     direction="BUY" if t.side > 0 else "SELL", strategy=t.strategy, entry=t.entry, price=cur,
                                     sl=p.sl, tp1=tps[0], tp2=tps[1], tp3=tps[2], tp_done=t.tp_done,
                                     volume=p.volume, volume_original=t.volume_orig, unrealized=round(p.unrealized_pnl, 2),
                                     realized=t.realized_pnl, remaining_risk=t.remaining_risk, managed=t.managed,
                                     minutes_open=round((now - _utc(t.opened_at)).total_seconds() / 60)))
        except BrokerError:
            acct = None
        loss = loss_status(self.store, acct.equity, self.cfg) if acct else None
        closed = sorted([t for t in self.store.trades() if t.state == "CLOSED"], key=lambda t: t.closed_at or "")[-30:]
        return dict(
            time=now.isoformat(), mode=self.mode_label, simulated=self.simulated, profile=self.cfg["profile"],
            broker=getattr(self.broker, "name", "?"), connected=self.connected, ready=self.ready, error=self.last_error,
            automated_trading=("STOPPED" if self.store.data["stopped"] else "WATCH ONLY" if self.monitor_only
                               else "PAUSED" if self.store.data["paused"] else "ENABLED"),
            lock=self.store.data.get("lock"), session=current_session(now), open_positions=len(rows), pending_orders=pending_n,
            account=(acct.to_dict() if acct else None), loss=loss, positions=rows,
            history=[dict(trade_id=t.trade_id, symbol=t.symbol, direction="BUY" if t.side > 0 else "SELL", strategy=t.strategy,
                          entry=t.entry, opened=t.opened_at, closed=t.closed_at, reason=t.close_reason,
                          volume=t.volume_orig, pnl=t.realized_pnl) for t in reversed(closed)],
            decisions=list(self.recent)[::-1])

    def write_status(self, now=None) -> None:
        if not self.status_path:
            return
        try:
            data = scrub(json.loads(json.dumps(self.status(now), default=str)))
            d = os.path.dirname(self.status_path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_status_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.status_path)
        except Exception as e:
            self.last_error = f"status not written: {e}"
