"""Offline demo of the whole trading system on the SIMULATED broker: no network, no account, no real money.

The signals here are SCRIPTED so each scenario is predictable. They are not from the strategies and show nothing about
how the strategies perform. The point is to watch the machinery: sizing, risk rejections, partial take-profits, the
daily loss lock, timeouts and restart recovery."""
from __future__ import annotations

import tempfile

import pandas as pd

from .audit import AuditLog
from .config import load
from .engine import TradingEngine
from .risk_manager import Proposal
from .sim import SimBroker
from .state import CommandQueue, StateStore

T0 = pd.Timestamp("2026-10-27 13:00", tz="UTC")     # a Tuesday, London and New York both open


def _quote(b: SimBroker, bid: float, ask: float, t) -> None:
    b.set_quote("GBPJPY", bid, ask, t)
    b.set_quote("USDJPY", 150.0, 150.02, t)
    b.set_quote("GBPUSD", 1.30, 1.3002, t)


def run(out=print, state_dir: str | None = None) -> dict:
    d = state_dir or tempfile.mkdtemp(prefix="fxbot_demo_")
    cfg = load("demo", path="/nonexistent")
    clock = [T0]
    broker = SimBroker()
    broker.connect()
    _quote(broker, 200.800, 200.815, T0)
    box: list = []
    sent: list[str] = []
    eng = TradingEngine(broker, cfg, StateStore(f"{d}/state.json"), AuditLog(f"{d}/audit.jsonl"), CommandQueue(f"{d}/cmd.jsonl"),
                        signals=lambda now: box, clock=lambda: clock[0], notify=sent.append, status_path=f"{d}/status.json")
    eng.start()
    out(f"{eng.mode_label}  (simulated broker, equity {broker.account().equity:,.2f})\n")

    def advance(minutes, bid=None):
        clock[0] = clock[0] + pd.Timedelta(minutes=minutes)
        if bid is not None:
            _quote(broker, bid, bid + 0.015, clock[0])
        else:
            q = broker.quote("GBPJPY")
            _quote(broker, q.bid, q.ask, clock[0])
        return eng.tick()

    def show_new():
        while sent:
            out("   >> " + sent.pop(0))

    out("1) A good signal: the risk manager sizes it from equity and approves it")
    box[:] = [Proposal("demo1", "GBPJPY", 1, "trendpull", "trend", "intraday", 200.815, 200.656, [200.975, 201.137, 201.35],
                       clock[0], 65, None, None, ["scripted demo signal"], ["trendpull"])]
    eng.tick(); show_new()
    t = eng.store.trades(open_only=True)[0]
    out(f"   volume {t.volume_orig} lots, planned partial closes {t.exit_volumes} (None = the rest), risk {t.remaining_risk}\n")

    out("2) Price rises: TP1 closes 40%, TP2 closes 30%, TP3 closes the rest")
    box[:] = []
    advance(5, 200.98); show_new()
    advance(5, 201.15); show_new()
    advance(5, 201.37); show_new()
    out(f"   balance now {broker.account().balance:,.2f}\n")

    out("3) A signal while the spread is far too wide is rejected, with the reason")
    q = broker.quote("GBPJPY"); _quote(broker, q.bid, q.bid + 0.06, clock[0] + pd.Timedelta(minutes=70)); clock[0] += pd.Timedelta(minutes=70)
    box[:] = [Proposal("demo2", "GBPJPY", 1, "donchian", "breakout", "swing", q.bid + 0.06, q.bid - 0.16, [q.bid + 0.4],
                       clock[0], 60, None, None, [], ["donchian"])]
    eng.tick()
    for r in eng.recent:
        if r["decision"] == "REJECTED":
            out("   " + " ".join(r["reasons"]))
    out("")

    out("4) The market gaps against a new trade: the day's loss passes the limit and the bot locks entries (limit set to 0.2% for the demo)")
    cfg["risk"]["max_daily_loss_pct"] = 0.2
    _quote(broker, 200.80, 200.815, clock[0])
    box[:] = [Proposal("demo3", "GBPJPY", -1, "trendpull", "trend", "intraday", 200.80, 200.96, [200.48], clock[0], 60, None, None, [], ["trendpull"])]
    clock[0] += pd.Timedelta(minutes=130)
    _quote(broker, 200.80, 200.815, clock[0]); eng.store.data["cooldowns"].clear()
    box[0].time = clock[0]
    eng.tick(); show_new()
    _quote(broker, 201.5, 201.515, clock[0] + pd.Timedelta(minutes=5)); clock[0] += pd.Timedelta(minutes=5)
    eng.tick(); show_new()
    out(f"   lock: {eng.store.data.get('lock')}\n")
    out("Audit log written to " + f"{d}/audit.jsonl")
    return dict(dir=d, balance=broker.account().balance, events=len(eng.audit.read()))
