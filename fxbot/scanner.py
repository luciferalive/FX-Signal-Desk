"""Live scanner: analyse each pair in each view, alert only on graded setups, never twice for the same one."""
from __future__ import annotations

import json
import os

import pandas as pd

from . import DEFAULT_SYMBOLS
from .analysis import analyze, format_text
from .data import fetch
from .modes import MODES
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
    cache: dict = {}
    now = pd.Timestamp.now(tz="UTC")
    fresh = []

    def get(sym, iv):
        if (sym, iv) not in cache:
            cache[(sym, iv)] = closed_only(fetch(sym, iv), iv, now)
        return cache[(sym, iv)]

    for sym in symbols:
        for mode in modes:
            m = MODES[mode]
            try:
                htf = get(sym, m["htf"]) if m["htf"] else None
                an = analyze(sym, mode, get(sym, m["interval"]), htf, m["htf_name"])
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
            (send if notify else print)(format_text(an))

    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    json.dump(sorted(seen)[-500:], open(state_path, "w"))
    if not fresh:
        print(f"[scan] {now:%Y-%m-%d %H:%M} UTC - checked {len(symbols)} pairs, no new graded setups")
    return fresh
