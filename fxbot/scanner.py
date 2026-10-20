"""Live scanner: analyse each pair in each view, alert only on graded setups, never twice for the same one."""
from __future__ import annotations

import json
import os

import pandas as pd

from . import DEFAULT_SYMBOLS
from .analysis import analyze, format_text
from .data import load_frames
from .modes import MODES
from .news import fetch_news, upcoming_high
from .notify import send

BAR_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}


def closed_only(df: pd.DataFrame, iv: str, now: pd.Timestamp) -> pd.DataFrame:
    """Drop the still-forming candle."""
    if df.index[-1] + pd.Timedelta(minutes=BAR_MIN[iv]) > now:
        return df.iloc[:-1]
    return df


def scan(symbols=None, modes=None, notify=True, min_grade="B", state_path=".state/seen.json") -> list[dict]:
    symbols = symbols or DEFAULT_SYMBOLS
    modes = modes or list(MODES)
    seen = set(json.load(open(state_path))) if os.path.exists(state_path) else set()
    now = pd.Timestamp.now(tz="UTC")
    fresh = []

    # one Yahoo download per timeframe for ALL pairs (much faster than pair by pair)
    need = sorted({MODES[m]["interval"] for m in modes} | {MODES[m]["htf"] for m in modes if MODES[m]["htf"]})
    frames = load_frames(symbols, need)
    news = fetch_news()
    events = news["events"] if news.get("ok") else []

    for sym in symbols:
        for mode in modes:
            m = MODES[mode]
            try:
                df = frames.get(sym, {}).get(m["interval"])
                if df is None or df.empty:
                    continue
                df = closed_only(df, m["interval"], now)
                htf = None
                if m["htf"]:
                    htf = frames.get(sym, {}).get(m["htf"])
                    htf = closed_only(htf, m["htf"], now) if htf is not None and not htf.empty else None
                an = analyze(sym, mode, df, htf, m["htf_name"])
            except Exception as e:
                print(f"[scan] {sym} {mode}: {e}")
                continue
            s = an["signal"]
            if not s or GRADE_RANK[s["grade"]] < GRADE_RANK[min_grade]:
                continue
            key = f"{sym}|{mode}|{s['strategy']}|{s['side']}|{s['time']}"
            if key in seen:
                continue
            seen.add(key)
            fresh.append(an)
            text = format_text(an)
            warn = upcoming_high(events, sym, now)
            if warn:
                text += "\n" + warn
            (send if notify else print)(text)

    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    json.dump(sorted(seen)[-500:], open(state_path, "w"))
    if not fresh:
        print(f"[scan] {now:%Y-%m-%d %H:%M} UTC - checked {len(symbols)} pairs, no new graded setups")
    return fresh
