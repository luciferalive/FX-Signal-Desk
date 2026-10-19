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
