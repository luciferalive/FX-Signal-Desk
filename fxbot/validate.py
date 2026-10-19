"""Replay the EXACT live signal logic (analysis.analyze incl. confirmation gate) over history,
as if running live: at every step it only sees closed candles up to that moment, and a signal is
entered on the next candle. Prints win rate / avg R / profit factor by grade, session and strategy.
"""
from __future__ import annotations

import pandas as pd

from .analysis import analyze
from .backtest import format_report, run_backtest
from .data import resample
from .modes import MODES
from .strategies import Signal

DUR = {"15m": pd.Timedelta(minutes=15), "4h": pd.Timedelta(hours=4), "1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}


def replay(df_src: pd.DataFrame, symbol: str, mode: str = "swing", step: int = 3, spread_pips: float = 1.0,
           min_ratio: float = 0.6, progress: bool = True):
    """df_src: 15m candles for scalp mode, 15m or 1h candles for swing / daily."""
    m = MODES[mode]
    base = df_src if mode == "scalp" else resample(df_src, m["interval"])
    htf_full = resample(df_src, m["htf"]) if m["htf"] else None
    bar = DUR[m["interval"]]
    htf_bar = DUR[m["htf"]] if m["htf"] else None
    win = {"scalp": 1500, "swing": 700, "daily": 500}[mode]
    first = 230 if mode == "daily" else 260
    sigs, grades, seen = [], {}, set()

    for k in range(first, len(base) - 1, step):
        now = base.index[k - 1] + bar  # moment the last candle closed
        htf = htf_full[htf_full.index + htf_bar <= now].iloc[-600:] if htf_full is not None else None
        an = analyze(symbol, mode, base.iloc[max(0, k - win): k], htf, m["htf_name"], strict=True, min_ratio=min_ratio)
        s = an["signal"]
        if not s:
            continue
        key = (s["strategy"], s["side"], s["time"])
        if key in seen:
            continue
        seen.add(key)
        t = base.index[k - 1]
        sigs.append(Signal(k - 1, t, symbol, s["strategy"], mode, 1 if s["side"] == "BUY" else -1, s["entry"],
                           s["sl"], s["tp2"], s["rr"], s["entry_type"], max(int(s["expiry"]), 1),
                           "", an["session"]))
        grades[(t, s["strategy"])] = s["grade"]
        if progress and len(sigs) % 25 == 0:
            print(f"  ...{len(sigs)} signals (bar {k}/{len(base)})", flush=True)

    res = run_backtest(base, sigs, symbol, spread_pips=spread_pips)
    if res.stats.get("trades", 0):
        res.trades["grade"] = [grades.get((t, st), "?") for t, st in zip(res.trades["signal_time"], res.trades["strategy"])]
    return res, base


def _group(trades: pd.DataFrame, col: str) -> str:
    g = trades.groupby(col)["r"].agg(trades="count", win_rate=lambda x: round(100 * (x > 0).mean(), 1),
                                      avg_R=lambda x: round(x.mean(), 3),
                                      pf=lambda x: round(x[x > 0].sum() / abs(x[x <= 0].sum()), 2) if (x <= 0).any() else float("inf"))
    return g.to_string()


def report(res, symbol: str, mode: str) -> str:
    out = [format_report(res, f"VALIDATION {symbol} {mode} (strict confirmations)")]
    if res.stats.get("trades", 0):
        t = res.trades
        out += ["", "by grade:", _group(t, "grade"), "", "by strategy:", _group(t, "strategy")]
        n = res.stats["trades"]
        verdict = ("Sample too small to trust (<100 trades). Use more history, e.g. Dukascopy CSV." if n < 100
                   else "Sample is usable, but still check it holds on a period you did not tune on.")
        out += ["", f"Note: {verdict}"]
    return "\n".join(out)
