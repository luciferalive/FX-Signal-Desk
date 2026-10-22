"""Market regime: is price trending, ranging or too volatile to trade?

Uses ADX (Wilder, 14) for trend strength and ATR relative to its own 100-bar median for volatility.
Everything is causal (a bar's label only uses data up to that bar), so strategies and the backtester can
use it without look-ahead. This is a classifier and filter, not a signal generator.

    volatile    ATR is more than 1.8x its 100-bar median (news spike, panic): stand aside
    trending    ADX >= 25
    ranging     ADX < 20
    transition  ADX between 20 and 25
    unknown     not enough bars yet
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr

TRENDING, RANGING, VOLATILE, TRANSITION, UNKNOWN = "trending", "ranging", "volatile", "transition", "unknown"


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's Average Directional Index."""
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def vol_ratio(df: pd.DataFrame, n: int = 14, base: int = 100) -> pd.Series:
    """Current ATR divided by its own rolling median (1.0 = normal)."""
    a = atr(df, n)
    return a / a.rolling(base, min_periods=30).median()


def regime_series(df: pd.DataFrame) -> np.ndarray:
    """One label per bar."""
    a, v = adx(df).to_numpy(), vol_ratio(df).to_numpy()
    out = np.full(len(df), UNKNOWN, dtype=object)
    ok = ~np.isnan(a)
    out[ok & (a < 20)] = RANGING
    out[ok & (a >= 20) & (a < 25)] = TRANSITION
    out[ok & (a >= 25)] = TRENDING
    out[ok & ~np.isnan(v) & (v > 1.8)] = VOLATILE
    return out


def current_regime(df: pd.DataFrame) -> dict:
    lab = regime_series(df)[-1]
    a, v = adx(df).iloc[-1], vol_ratio(df).iloc[-1]
    return dict(label=str(lab), adx=None if np.isnan(a) else round(float(a), 1),
                vol_ratio=None if np.isnan(v) else round(float(v), 2),
                compressed=bool(not np.isnan(v) and v < 0.7))
