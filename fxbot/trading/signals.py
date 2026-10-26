"""Turns the existing signal engine's output into Proposals for the arbitration engine and the risk manager.
Strategies are not changed and never see a broker. Entries are MARKET entries: a signal whose entry price is no
longer near the market is rejected by the risk manager (it does not chase)."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from ..strategies import STRATEGIES
from .risk_manager import Proposal


def signal_id(symbol: str, strategy: str, side: int, when) -> str:
    raw = f"{symbol}|{strategy}|{side}|{pd.Timestamp(when).isoformat()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _style(key: str, default: str = "intraday") -> str:
    cls = STRATEGIES.get(key)
    return getattr(cls, "style", default) if cls else default


def from_workflow(result: dict) -> Proposal | None:
    """The final BUY / SELL of the 1h plan > 15m setup > 5m execution workflow. WAIT / NO TRADE gives None."""
    sg = result.get("signal") if result else None
    if not sg or sg.get("direction") not in ("BUY", "SELL"):
        return None
    side = 1 if sg["direction"] == "BUY" else -1
    key = sg.get("strategy_key") or sg["strategy"]
    return Proposal(signal_id(sg["instrument"], key, side, sg["timestamp"]), sg["instrument"], side, key,
                    sg.get("family", ""), _style(key, "scalp" if "15m" in str(sg.get("timeframe", "")) else "intraday"),
                    float(sg["entry"]), float(sg["stop_loss"]), [float(x) for x in sg["take_profit"]],
                    pd.Timestamp(sg["timestamp"]), float(sg.get("confidence") or 0), None, None,
                    list(sg.get("explanation", [])), [key])


def from_analysis(symbol: str, an: dict) -> Proposal | None:
    """A swing / daily signal from analysis.analyze(). Only signals that are still valid market entries are proposed."""
    s = an.get("signal") if an else None
    if not s:
        return None
    side = 1 if s["side"] == "BUY" else -1
    key = s.get("key") or s["strategy"]
    return Proposal(signal_id(symbol, key, side, s["time"]), symbol, side, key, s.get("family", ""), _style(key, "swing"),
                    float(s["entry"]), float(s["sl"]), [float(s["tp1"]), float(s["tp2"])], pd.Timestamp(s["time"]),
                    float(s.get("score", 0)) / max(1, s.get("of", 1)) * 100, None, None, list(s.get("reasons", [])), [key])


class FrameMarket:
    """ATR and swing levels from candle frames, for the trade manager's trailing stops."""

    def __init__(self, frames_of, tf: str = "15m"):
        self.frames_of, self.tf = frames_of, tf

    def _df(self, symbol: str):
        fr = self.frames_of(symbol) or {}
        return fr.get(self.tf)

    def atr(self, symbol: str) -> float | None:
        df = self._df(symbol)
        if df is None or len(df) < 20:
            return None
        v = float(atr_series(df).iloc[-1])
        return v if np.isfinite(v) else None

    def swing(self, symbol: str, side: int, n: int = 12) -> float | None:
        df = self._df(symbol)
        if df is None or len(df) < n:
            return None
        return float(df["low"].iloc[-n:].min()) if side > 0 else float(df["high"].iloc[-n:].max())

    def strategy_trail(self, trade) -> float | None:
        return None   # strategy-specific trailing is an extension point: strategies do not provide one yet
