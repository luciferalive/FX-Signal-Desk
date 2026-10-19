"""Smart-money zones and reference prices: fair value gaps, order blocks, premium/discount range,
daily/weekly/midnight opens, moving averages. Every zone carries the time it formed (t0) so the
chart can draw it where it happened. Only closed candles are used."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, pivot_idx, sma
from .sessions import NY


def _iso(df, i):
    return df.index[int(i)].isoformat()


def find_fvgs(df: pd.DataFrame, lookback: int = 160, min_atr: float = 0.15, disp: float = 0.5,
              keep: int = 8, max_dist_atr: float = 15.0) -> list[dict]:
    """Unfilled 3-candle fair value gaps with a displacement candle in the middle.
    Bullish: high of candle 1 < low of candle 3. Bearish: low of candle 1 > high of candle 3.
    A gap is dropped once a candle CLOSES through its far edge."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr(df).to_numpy()
    n = len(df)
    price, a_now = c[-1], a[-1]
    out = []
    for i in range(max(2, n - lookback), n):
        ai = a[i - 1]
        if np.isnan(ai) or abs(c[i - 1] - o[i - 1]) < disp * ai:
            continue
        if l[i] > h[i - 2] and c[i - 1] > o[i - 1]:
            side, bottom, top = 1, h[i - 2], l[i]
        elif h[i] < l[i - 2] and c[i - 1] < o[i - 1]:
            side, bottom, top = -1, h[i], l[i - 2]
        else:
            continue
        if top - bottom < min_atr * ai:
            continue
        status, dead = "fresh", False
        for j in range(i + 1, n):
            if side == 1:
                if c[j] < bottom:
                    dead = True
                    break
                if l[j] <= top:
                    status = "tested"
            else:
                if c[j] > top:
                    dead = True
                    break
                if h[j] >= bottom:
                    status = "tested"
        if dead:
            continue
        mid = (top + bottom) / 2
        if abs(mid - price) > max_dist_atr * a_now:
            continue
        out.append(dict(kind="FVG", side=side, top=float(top), bottom=float(bottom), status=status,
                        t0=_iso(df, i - 2), dist=abs(mid - price)))
    out.sort(key=lambda z: z["dist"])
    return out[:keep]


def find_obs(df: pd.DataFrame, lookback: int = 160, disp: float = 1.3, keep: int = 5,
             max_dist_atr: float = 15.0) -> list[dict]:
    """Order blocks: the last opposite candle before a displacement candle (body >= disp * ATR).
    Dropped once a candle closes through the block."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr(df).to_numpy()
    n = len(df)
    price, a_now = c[-1], a[-1]
    out = []
    for k in range(max(2, n - lookback), n - 1):
        ak = a[k - 1]
        if np.isnan(ak) or h[k - 1] - l[k - 1] < 0.3 * ak:
            continue
        if c[k] - o[k] >= disp * ak and c[k - 1] < o[k - 1]:
            side, bottom, top = 1, l[k - 1], h[k - 1]
        elif o[k] - c[k] >= disp * ak and c[k - 1] > o[k - 1]:
            side, bottom, top = -1, l[k - 1], h[k - 1]
        else:
            continue
        status, dead = "fresh", False
        for j in range(k + 1, n):
            if side == 1:
                if c[j] < bottom:
                    dead = True
                    break
                if l[j] <= top:
                    status = "tested"
            else:
                if c[j] > top:
                    dead = True
                    break
                if h[j] >= bottom:
                    status = "tested"
        if dead or abs((top + bottom) / 2 - price) > max_dist_atr * a_now:
            continue
        out.append(dict(kind="OB", side=side, top=float(top), bottom=float(bottom), status=status,
                        t0=_iso(df, k - 1), dist=abs((top + bottom) / 2 - price)))
    out.sort(key=lambda z: z["dist"])
    return out[:keep]


def dealing_range(df: pd.DataFrame, n: int = 4, lookback: int = 150):
    """Latest confirmed swing high and low = current dealing range. Above 50% = premium
    (look for sells), below = discount (look for buys)."""
    d = df.iloc[-lookback:]
    hi_i = pivot_idx(d["high"].to_numpy(), n, "max")
    lo_i = pivot_idx(d["low"].to_numpy(), n, "min")
    if not hi_i or not lo_i:
        return None
    hi, lo = float(d["high"].iloc[hi_i[-1]]), float(d["low"].iloc[lo_i[-1]])
    if hi <= lo:
        return None
    eq = (hi + lo) / 2
    price = float(df["close"].iloc[-1])
    t0 = d.index[min(hi_i[-1], lo_i[-1])].isoformat()
    return dict(hi=hi, lo=lo, eq=eq, zone="premium" if price > eq else "discount", t0=t0)


def open_levels(df: pd.DataFrame) -> list[dict]:
    """Daily open (FX day rolls 17:00 NY), midnight open (00:00 NY, ICT 'true day open'), weekly open."""
    idx = df.index
    ny = idx.tz_convert(NY)
    shifted = ny + pd.Timedelta(hours=7)
    out = []
    day = np.asarray(shifted.normalize() == shifted.normalize()[-1])
    i = int(day.argmax())
    out.append(dict(name="Daily Open", price=float(df["open"].iloc[i]), kind="open", t0=_iso(df, i)))
    cal = np.asarray(ny.normalize() == ny.normalize()[-1])
    j = int(cal.argmax())
    if ny[j].hour == 0 and ny[j].minute == 0:
        out.append(dict(name="Midnight Open", price=float(df["open"].iloc[j]), kind="open", t0=_iso(df, j)))
    wk = shifted.tz_localize(None).to_period("W-SUN")
    w = np.asarray(wk == wk[-1])
    k = int(w.argmax())
    out.append(dict(name="Weekly Open", price=float(df["open"].iloc[k]), kind="open", t0=_iso(df, k)))
    return out


def sma_values(df: pd.DataFrame, periods=(50, 100, 200)) -> dict:
    c = df["close"]
    out = {}
    for p in periods:
        v = sma(c, p).iloc[-1] if len(df) >= p else np.nan
        out[p] = None if np.isnan(v) else float(v)
    return out


def sma_series(df: pd.DataFrame, n_last: int, periods=(50, 100, 200), dec: int = 5) -> dict:
    c = df["close"]
    out = {}
    for p in periods:
        s = sma(c, p).iloc[-n_last:]
        out[str(p)] = [None if np.isnan(v) else round(float(v), dec + 1) for v in s]
    return out
