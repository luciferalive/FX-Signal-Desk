from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def swing_points(df: pd.DataFrame, n: int = 3):
    """Latest *confirmed* swing high / low (forward-filled).

    A pivot at bar p needs n bars on each side, so it is only known at bar p+n.
    We shift by n, which removes lookahead bias.
    """
    win = 2 * n + 1
    is_hi = df["high"] == df["high"].rolling(win, center=True).max()
    is_lo = df["low"] == df["low"].rolling(win, center=True).min()
    sh = df["high"].where(is_hi).shift(n).ffill()
    sl = df["low"].where(is_lo).shift(n).ffill()
    return sh, sl


def pivot_idx(arr, n: int, mode: str) -> list[int]:
    """Confirmed pivot indices (needs n bars on each side). mode 'max' or 'min'."""
    s = pd.Series(np.asarray(arr, dtype=float))
    win = 2 * n + 1
    ref = s.rolling(win, center=True).max() if mode == "max" else s.rolling(win, center=True).min()
    out: list[int] = []
    for i in np.where((s == ref).to_numpy())[0]:
        if not out or i - out[-1] > n:
            out.append(int(i))
    return out


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def pivot_idx(arr, n: int, mode: str) -> list[int]:
    """Confirmed pivot indices (needs n bars on each side). mode 'max' or 'min'.
    A pivot at bar p only depends on bars p-n..p+n, so it is known at bar p+n."""
    import numpy as np

    s = pd.Series(arr)
    win = 2 * n + 1
    ref = s.rolling(win, center=True).max() if mode == "max" else s.rolling(win, center=True).min()
    idx = np.where((s == ref).to_numpy())[0]
    out: list[int] = []
    for i in idx:
        if not out or i - out[-1] > n:
            out.append(int(i))
    return out


# ---------------------------------------------------------------- more indicators (all causal)
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    """(middle, upper, lower, bandwidth) where bandwidth = (upper - lower) / middle."""
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return mid, up, lo, (up - lo) / mid


def stochastic(df: pd.DataFrame, k: int = 14, smooth: int = 7, d: int = 3):
    """Slow stochastic (14, 7, 3): returns (%K smoothed, %D)."""
    lo, hi = df["low"].rolling(k).min(), df["high"].rolling(k).max()
    raw = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    kk = raw.rolling(smooth).mean()
    return kk, kk.rolling(d).mean()


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi candles. close = (O+H+L+C)/4, open = (prev HA open + prev HA close)/2."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    hc = (o + h + l + c) / 4
    ho = np.empty(len(df))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(df)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return pd.DataFrame({"open": ho, "high": np.maximum.reduce([h, ho, hc]), "low": np.minimum.reduce([l, ho, hc]),
                         "close": hc}, index=df.index)


def donchian(df: pd.DataFrame, n: int = 20):
    """Highest high / lowest low of the PREVIOUS n bars (excludes the current bar, so a close above it is a breakout)."""
    return df["high"].shift(1).rolling(n).max(), df["low"].shift(1).rolling(n).min()
