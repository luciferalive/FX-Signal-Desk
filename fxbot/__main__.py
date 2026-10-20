from __future__ import annotations

import argparse
import os

from . import DEFAULT_SYMBOLS
from .backtest import format_report, run_backtest
from .data import fetch, load_csv, resample, synthetic
from .modes import MODES
from .strategies import STRATEGIES

MODE_CHOICES = ["both", *MODES]  # "both" = every view


def _combos(strategy: str, mode: str):
    """(strategy, mode) pairs, skipping views a strategy does not support."""
    names = list(STRATEGIES) if strategy == "all" else [strategy]
    modes = list(MODES) if mode == "both" else [mode]
    return [(n, m) for n in names for m in modes if m in STRATEGIES[n].PRESETS]


def _synthetic_for(iv: str):
    return synthetic(iv, days=900 if iv == "1d" else 300)


def cmd_backtest(a):
    os.makedirs("reports", exist_ok=True)
    summary = []
    for sym in a.symbol:
        for name, mode in _combos(a.strategy, a.mode):
            strat = STRATEGIES[name](mode)
            iv = strat.preset["interval"]
            if a.synthetic:
                df = _synthetic_for(iv)
            elif a.csv:
                df = load_csv(a.csv, iv)
            else:
                df = fetch(sym, iv)
            res = run_backtest(df, strat.generate(df, sym), sym, risk_pct=a.risk, spread_pips=a.spread)
            title = f"{sym} {name}/{mode} ({iv}, {len(df)} bars)"
            print(format_report(res, title), "\n")
            summary.append((title, res.stats))
            if not a.no_chart and res.stats.get("trades", 0):
                try:
                    from .charts import make_chart
                    path = f"reports/{sym}_{name}_{mode}.html"
                    make_chart(df, res.trades, title, path)
                    print(f"  chart -> {path}\n")
                except ImportError:
                    print("  (pip install plotly for charts)")
    print("SUMMARY")
    for t, s in summary:
        print(f"  {t:<52} trades={s.get('trades')}  WR={s.get('win_rate')}%  avgR={s.get('avg_R')}  PF={s.get('profit_factor')}  DD={s.get('max_drawdown_pct')}%")


def main():
    ap = argparse.ArgumentParser(prog="fxbot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def bt_args(p):
        p.add_argument("--symbol", nargs="+", default=["EURUSD"])
        p.add_argument("--strategy", default="all", choices=["all", *STRATEGIES])
        p.add_argument("--mode", default="both", choices=MODE_CHOICES)
        p.add_argument("--csv")
        p.add_argument("--synthetic", action="store_true")
        p.add_argument("--risk", type=float, default=1.0, help="%% equity risked per trade")
        p.add_argument("--spread", type=float, default=1.0, help="spread in pips")
        p.add_argument("--no-chart", action="store_true")

    bt_args(sub.add_parser("backtest"))
    bt_args(sub.add_parser("demo", help="offline run on synthetic data"))

    sc = sub.add_parser("scan", help="look for live setups and alert")
    sc.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    sc.add_argument("--mode", default="both", choices=MODE_CHOICES)
    sc.add_argument("--no-notify", action="store_true")
    sc.add_argument("--min-grade", default="B", choices=["A", "B", "C"], help="lowest grade worth an alert")

    an = sub.add_parser("analyze", help="full analysis + signal for one pair")
    an.add_argument("--symbol", default="EURUSD")
    an.add_argument("--mode", default="scalp", choices=list(MODES))
    an.add_argument("--synthetic", action="store_true")

    va = sub.add_parser("validate", help="replay the live signal logic over history; win rate by grade")
    va.add_argument("--symbol", default="EURUSD")
    va.add_argument("--mode", default="swing", choices=list(MODES))
    va.add_argument("--csv", help="15m (or finer) OHLC CSV in UTC, e.g. from Dukascopy")
    va.add_argument("--synthetic", action="store_true")
    va.add_argument("--step", type=int, default=3, help="bars between checks (keep <=3 for scalp)")
    va.add_argument("--spread", type=float, default=1.0)
    va.add_argument("--min-ratio", type=float, default=0.6, help="share of confirmations required (0.6 = 60%%)")

    db = sub.add_parser("dashboard", help="build the web dashboard into the docs folder")
    db.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    db.add_argument("--out", default="docs", help="output folder (index.html + data/*.json)")
    db.add_argument("--demo", action="store_true", help="made-up prices, labelled as demo, no internet needed")

    sub.add_parser("telegram-test", help="send a test message to your Telegram")
    wh = sub.add_parser("webhook")
    wh.add_argument("--port", type=int, default=int(os.getenv("PORT", 8080)))

    a = ap.parse_args()
    if a.cmd == "demo":
        a.synthetic = True
        cmd_backtest(a)
    elif a.cmd == "backtest":
        cmd_backtest(a)
    elif a.cmd == "scan":
        from .scanner import scan
        scan(a.symbols, modes=list(MODES) if a.mode == "both" else [a.mode], notify=not a.no_notify, min_grade=a.min_grade)
    elif a.cmd == "analyze":
        from .analysis import analyze, format_text
        m = MODES[a.mode]
        if a.synthetic:
            base = synthetic("15m", days=900)
            df = base if a.mode == "scalp" else resample(base, m["interval"])
            htf = resample(base, m["htf"]) if m["htf"] else None
        else:
            df = fetch(a.symbol, m["interval"])
            htf = fetch(a.symbol, m["htf"]) if m["htf"] else None
        print(format_text(analyze(a.symbol, a.mode, df, htf, m["htf_name"])))
    elif a.cmd == "validate":
        from .validate import replay, report
        if a.synthetic:
            df = synthetic("15m", days=900 if a.mode != "scalp" else 120)
        elif a.csv:
            df = load_csv(a.csv, "15m")
        else:
            df = fetch(a.symbol, "15m" if a.mode == "scalp" else "1h")
        res, _ = replay(df, a.symbol, a.mode, a.step, a.spread, a.min_ratio)
        print(report(res, a.symbol, a.mode))
    elif a.cmd == "dashboard":
        from .dashboard import build, demo_frames
        from .news import sample_news
        out = os.path.dirname(a.out) or "." if a.out.endswith(".html") else a.out
        if a.demo:
            path = build(a.symbols, out, frames=demo_frames(a.symbols), news=sample_news(), demo=True, inline=True)
        else:
            path = build(a.symbols, out)
        print(f"dashboard -> {path}")
    elif a.cmd == "telegram-test":
        from .notify import telegram_test
        raise SystemExit(0 if telegram_test() else 1)
    elif a.cmd == "webhook":
        from .webhook import serve
        serve(a.port)


if __name__ == "__main__":
    main()
