"""Common strategy interface and the standard Signal object.

Every strategy:
  * has a name, a family (used so that two indicators measuring the same thing do not count as two
    confirmations), a trading style and a list of required data;
  * has presets per mode (scalp = 15m, swing = 4h, daily = 1d) that can be overridden;
  * returns Signal objects built ONLY from data up to the signal bar (no look-ahead);
  * explains each signal (reason) and says when it is no longer valid (invalidation).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..indicators import atr
from ..regime import regime_series
from ..sessions import session_labels


_CTX: dict = {}


@dataclass
class Signal:
    idx: int
    time: pd.Timestamp
    symbol: str
    strategy: str
    mode: str
    side: int  # +1 long, -1 short
    entry: float
    sl: float
    tp: float
    rr: float
    entry_type: str = "limit"  # "limit" | "market"
    expiry: int = 6  # bars a limit order stays alive
    reason: str = ""
    session: str = ""
    # --- standard fields every strategy fills in ---
    family: str = ""
    timeframe: str = ""
    regime: str = ""
    invalidation: str = ""
    confidence: float | None = None  # only set by the confluence engine (a heuristic score, not a probability)

    @property
    def direction(self) -> str:
        return "BUY" if self.side == 1 else "SELL"

    @property
    def targets(self) -> list[float]:
        """TP1 at 1R, TP2 at the strategy target."""
        return [self.entry + self.side * abs(self.entry - self.sl), self.tp]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = pd.Timestamp(self.time).isoformat()
        d["direction"] = self.direction
        d["targets"] = self.targets
        return d


class Strategy:
    name = ""  # registry key
    label = ""  # human name
    family = ""  # trend | breakout | price_action | mean_reversion | scalping | swing | ict
    style = "intraday"  # scalp | intraday | swing
    summary = ""
    requires = ("OHLC candles",)
    PRESETS: dict = {}

    def __init__(self, mode: str = "swing", **overrides):
        if mode not in self.PRESETS:
            raise ValueError(f"{self.name}: mode must be one of {list(self.PRESETS)}")
        self.mode = mode
        self.preset = {**self.PRESETS[mode], **overrides}

    # ------------------------------------------------------------ to implement
    def generate(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        raise NotImplementedError

    # ------------------------------------------------------------ shared helpers
    def context(self, df: pd.DataFrame):
        """(atr array, session labels, regime labels) used by most strategies. Cached for the last frame so that
        running a dozen strategies on the same candles computes ATR / ADX / sessions only once."""
        key = (id(df), len(df), df.index[-1], float(df["close"].iloc[-1]), float(df["close"].iloc[0]))
        if _CTX.get("key") != key:
            _CTX.update(key=key, val=(atr(df).to_numpy(), session_labels(df.index), regime_series(df)))
        return _CTX["val"]

    def make(self, df, i, symbol, side, entry, sl, *, a, sess, reg, rr=None, tp=None, entry_type="market",
             expiry=0, reason="", invalidation="", min_stop=0.5, max_stop=3.5) -> Signal | None:
        """Build a Signal after basic sanity checks. Returns None when the trade makes no sense
        (stop on the wrong side, absurdly tight or wide stop, reward-to-risk below the preset minimum)."""
        risk = (entry - sl) * side
        if not (risk > 0 and min_stop * a[i] <= risk <= max_stop * a[i]):
            return None
        if tp is not None:
            rr = (tp - entry) * side / risk
        else:
            tp = entry + side * rr * risk
        if rr < self.preset.get("min_rr", 1.0):
            return None
        return Signal(i, df.index[i], symbol, self.name, self.mode, side, float(entry), float(sl), float(tp),
                      round(float(rr), 2), entry_type, expiry, reason, str(sess[i]), self.family,
                      self.preset.get("interval", ""), str(reg[i]),
                      invalidation or ("Invalid if price closes beyond the stop-loss"
                                       + (f", or the limit is not filled within {expiry} bars" if entry_type == "limit" else "")))

    @classmethod
    def describe(cls) -> dict:
        return dict(name=cls.name, label=cls.label or cls.name, family=cls.family, style=cls.style,
                    summary=cls.summary, requires=list(cls.requires), modes=list(cls.PRESETS))
