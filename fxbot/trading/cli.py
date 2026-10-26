"""Command line for the trading system:  python -m fxbot trade <action> [options]

  demo          offline walk-through on the simulated broker (no account needed)
  status        connect to the broker in trading.json and show the account (a safe connection test)
  run           run the engine (DEMO by default). Live needs --profile live AND a typed confirmation, every time
  panel         open the local control panel at http://127.0.0.1:8765
  history       recent decisions and closed trades
  pause | resume | stop | start | unlock | close | close-all | liquidate     control a running engine
"""
from __future__ import annotations

import json
import os
import time

import pandas as pd

from .. import DEFAULT_SYMBOLS
from .audit import AuditLog
from .config import LIVE_PHRASE, load
from .engine import TradingEngine, TradingSafetyError
from .factory import make_broker
from .models import BrokerError
from .state import CommandQueue, StateStore

STATE_DIR = ".state"


def paths(profile: str, state_dir: str = STATE_DIR) -> dict:
    return dict(state=f"{state_dir}/trading_{profile}.json", audit=f"{state_dir}/audit_{profile}.jsonl",
                status=f"{state_dir}/status_{profile}.json", commands=f"{state_dir}/commands_{profile}.jsonl")


class LiveSignals:
    """Candidates from the existing signal engine, refreshed at most once a minute. Free Yahoo data is delayed and is NOT
    the broker's own price feed, so the risk manager re-checks every signal against the broker's current quote."""

    def __init__(self, symbols: list[str], modes: list[str], main_cfg: dict, refresh_s: int = 60):
        self.symbols, self.modes, self.main, self.refresh_s = symbols, modes, main_cfg, refresh_s
        self.frames: dict = {}
        self._at = 0.0
        self.events: list = []
        self._news_at = 0.0

    def _load(self, now) -> None:
        from ..data import load_frames
        from ..modes import MODES
        from ..scanner import closed_only
        wf = self.main["workflow"]
        need = {wf["plan"], wf["setup"], wf["execute"]}
        for m in self.modes:
            if m != "scalp":
                need |= {MODES[m]["interval"]} | ({MODES[m]["htf"]} if MODES[m]["htf"] else set())
        raw = load_frames(self.symbols, sorted(need))
        self.frames = {s: {tf: closed_only(d, tf, now) for tf, d in fr.items() if d is not None and not d.empty}
                       for s, fr in raw.items()}
        self._at = time.time()

    def __call__(self, now):
        from ..analysis import analyze
        from ..modes import MODES
        from ..workflow import evaluate
        from .signals import from_analysis, from_workflow
        if time.time() - self._at > self.refresh_s:
            self._load(now)
        out = []
        for sym in self.symbols:
            fr = self.frames.get(sym) or {}
            try:
                if "scalp" in self.modes:
                    out.append(from_workflow(evaluate(sym, fr, self.main, self.events, now)))
                for m in self.modes:
                    if m == "scalp":
                        continue
                    spec = MODES[m]
                    df, htf = fr.get(spec["interval"]), fr.get(spec["htf"]) if spec["htf"] else None
                    if df is not None and not df.empty:
                        out.append(from_analysis(sym, analyze(sym, m, df, htf, spec["htf_name"])))
            except Exception as e:
                print(f"[signals] {sym}: {type(e).__name__}: {e}")
        return [p for p in out if p]

    def get_events(self):
        if time.time() - self._news_at > 600:
            from ..news import fetch_news
            n = fetch_news()
            self.events = n["events"] if n.get("ok") else self.events
            self._news_at = time.time()
        return self.events


def _paper_feed(broker, signals: LiveSignals, main_cfg: dict, now) -> None:
    """The simulated broker has no market: give it the latest candle close from the free data, with a typical spread."""
    from ..backtest import pip_size
    from ..config import spread_pips
    for sym, fr in signals.frames.items():
        df = fr.get("5m")
        if df is None or df.empty:
            continue
        px = float(df["close"].iloc[-1])
        half = spread_pips(main_cfg, sym) * pip_size(sym) / 2
        broker.set_quote(sym, px - half, px + half, now)


def _summary(acct) -> str:
    return (f"{'LIVE' if acct.is_live else 'DEMO'} ACCOUNT  broker={acct.broker} id={str(acct.account_id)[:3]}***  "
            f"balance={acct.balance:,.2f} equity={acct.equity:,.2f} {acct.currency}  free margin={acct.margin_free:,.2f}")


def cmd_status(a) -> int:
    cfg = load(a.profile)
    broker = make_broker(cfg)
    try:
        broker.connect()
        acct = broker.account()
    except BrokerError as e:
        print(f"DISCONNECTED: {e}")
        return 1
    print(_summary(acct))
    if cfg["profile"] == "demo" and acct.is_live:
        print("WARNING: the DEMO profile is selected but this is a LIVE account. The engine will refuse to run.")
    if cfg["profile"] == "live" and not acct.is_live:
        print("NOTE: the LIVE profile is selected but this is a demo account. The engine will refuse to run.")
    return 0


def cmd_run(a) -> int:
    from ..config import load as load_main
    from ..notify import send
    from .signals import FrameMarket
    cfg = load(a.profile)
    main = load_main()
    p = paths(a.profile)
    broker = make_broker(cfg)
    armed = False
    if a.profile == "live":
        try:
            broker.connect()
            acct = broker.account()
        except BrokerError as e:
            print(f"DISCONNECTED: {e}")
            return 1
        print(_summary(acct))
        print("LIVE risk settings:", json.dumps(cfg["risk"]))
        if not acct.is_live:
            print("REFUSED: this is not a live account.")
            return 2
        print(f"\nLive trading uses REAL MONEY. To arm it for this run only, type exactly:\n  {LIVE_PHRASE}")
        armed = input("> ").strip() == LIVE_PHRASE
        print("LIVE trading ARMED for this run." if armed else "Not armed: the bot will only watch, no orders.")
    symbols = [s.upper() for s in (a.symbols or DEFAULT_SYMBOLS)]
    modes = cfg.get("signals", {}).get("modes", ["scalp"])
    sig = LiveSignals(symbols, modes, main)
    eng = TradingEngine(broker, cfg, StateStore(p["state"]), AuditLog(p["audit"]), CommandQueue(p["commands"]),
                        signals=sig, events=sig.get_events, market=FrameMarket(lambda s: sig.frames.get(s, {}), main["workflow"]["setup"]),
                        notify=send, armed_live=armed, main_cfg=main, status_path=p["status"])
    try:
        ok = eng.start()
    except TradingSafetyError as e:
        print("REFUSED:", e)
        return 2
    print(f"{eng.mode_label}  profile={a.profile}  broker={broker.name}  ready={ok}  {eng.last_error}")
    if broker.name == "sim":
        print("NOTE: broker 'sim' = PAPER trading with simulated fills on delayed free prices. It is not a broker account.")
    while True:
        now = pd.Timestamp.now(tz="UTC")
        try:
            if broker.name == "sim":
                sig(now)                       # refresh frames
                _paper_feed(broker, sig, main, now)
            res = eng.tick()
            if res.get("note"):
                print(f"[{now:%H:%M:%S}] {res['note']}")
        except Exception as e:   # the loop must survive anything; the audit log records it
            eng.audit.write("tick_exception", now, error=f"{type(e).__name__}: {e}")
            print(f"[engine] {type(e).__name__}: {e}")
        if a.once:
            return 0
        try:
            time.sleep(cfg["execution"]["loop_seconds"])
        except KeyboardInterrupt:
            print("Stopped. Open trades keep their broker-side stop-loss and final take-profit; partial take-profits and "
                  "trailing only work while the bot is running.")
            return 0


def cmd_control(a, action: str) -> int:
    cfg_profile = a.profile
    q = CommandQueue(paths(cfg_profile)["commands"])
    args = {}
    if action in ("close", "close_all", "liquidate"):
        if cfg_profile == "live":
            if input("LIVE account. Type CLOSE to confirm: ").strip() != "CLOSE":
                print("Cancelled.")
                return 1
        args["confirm"] = True
    if action == "close":
        if not a.position:
            print("Use --position <id> (see the panel or 'trade history').")
            return 1
        args["position_id"] = a.position
    cid = q.push(action, **args)
    print(f"Queued '{action}' ({cid}). A running engine applies it on its next tick (about {load(cfg_profile)['execution']['loop_seconds']} s).")
    return 0


def cmd_history(a) -> int:
    p = paths(a.profile)
    st = StateStore(p["state"])
    closed = sorted([t for t in st.trades() if t.state == "CLOSED"], key=lambda t: t.closed_at or "")[-20:]
    print("CLOSED TRADES")
    for t in closed:
        print(f"  {t.closed_at[:16]} {t.symbol:7} {'BUY ' if t.side > 0 else 'SELL'} {t.strategy:12} vol {t.volume_orig:<5} "
              f"P&L {t.realized_pnl:+9.2f}  {t.close_reason}")
    if not closed:
        print("  (none yet)")
    print("\nRECENT DECISIONS (why the bot entered or rejected)")
    for r in AuditLog(p["audit"]).read(last=400, event="decision")[-15:]:
        s = r.get("signal", {})
        print(f"  {r['time'][11:19]} {s.get('symbol', ''):7} {s.get('strategy', ''):12} {r.get('decision', '')}: "
              f"{' '.join(r.get('reasons') or [])}")
    return 0


def main(a) -> int:
    act = a.action
    if act == "demo":
        from .demo import run
        run()
        return 0
    if act == "status":
        return cmd_status(a)
    if act == "run":
        return cmd_run(a)
    if act == "history":
        return cmd_history(a)
    if act == "panel":
        from .panel import serve
        serve(a.profile, STATE_DIR, a.port, live=a.profile == "live")
        return 0
    mapping = {"pause": "pause", "resume": "resume", "stop": "stop", "start": "start", "unlock": "unlock",
               "close": "close", "close-all": "close_all", "liquidate": "liquidate"}
    return cmd_control(a, mapping[act])
