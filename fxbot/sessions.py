"""Session / killzone marking. Everything is defined in New York local time
(the ICT convention) so DST is handled automatically."""
from __future__ import annotations

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

# (start_hour, end_hour) in New York local time, decimal hours
WINDOWS = {
    "Asia": (19.0, 24.0),
    "London KZ": (2.0, 5.0),
    "NY AM KZ": (7.0, 10.0),
    "London Close": (10.0, 12.0),
    "NY PM KZ": (13.5, 16.0),
}
DEFAULT_KZ = ("London KZ", "NY AM KZ")


def _hours(index: pd.DatetimeIndex) -> np.ndarray:
    ny = index.tz_convert(NY)
    return (ny.hour + ny.minute / 60.0).to_numpy()


def window_mask(index: pd.DatetimeIndex, name: str) -> np.ndarray:
    s, e = WINDOWS[name]
    h = _hours(index)
    return (h >= s) & (h < e)


def killzone_mask(index: pd.DatetimeIndex, names=DEFAULT_KZ) -> np.ndarray:
    m = np.zeros(len(index), dtype=bool)
    for n in names:
        m |= window_mask(index, n)
    return m


def session_labels(index: pd.DatetimeIndex) -> np.ndarray:
    """Label every bar with its session (priority: killzones first)."""
    out = np.full(len(index), "Off-hours", dtype=object)
    for name in ("Asia", "London Close", "NY PM KZ", "NY AM KZ", "London KZ"):
        out[window_mask(index, name)] = name
    return out


def day_key(index: pd.DatetimeIndex) -> pd.Series:
    """Forex trading day id (rolls at 17:00 New York)."""
    ny = index.tz_convert(NY)
    return pd.Series((ny + pd.Timedelta(hours=7)).normalize().tz_localize(None), index=index)


def day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Asian-range high/low and prior-day high/low, aligned to df.index.

    The forex 'day' rolls at 17:00 New York. The Asian range (19:00-00:00 NY)
    is only exposed after it has completed (00:00-17:00 NY) -> no lookahead.
    """
    idx = df.index
    ny = idx.tz_convert(NY)
    key = pd.Series((ny + pd.Timedelta(hours=7)).normalize().tz_localize(None), index=idx)
    hrs = pd.Series(ny.hour + ny.minute / 60.0, index=idx)

    asia = window_mask(idx, "Asia")
    if asia.any():
        g = df[asia].groupby(key[asia])
        ah = key.map(g["high"].max())
        al = key.map(g["low"].min())
    else:
        ah = pd.Series(np.nan, index=idx)
        al = pd.Series(np.nan, index=idx)
    valid = hrs < 17
    ah, al = ah.where(valid), al.where(valid)

    daily = df.groupby(key).agg(high=("high", "max"), low=("low", "min")).shift(1)
    return pd.DataFrame(
        {"asia_h": ah, "asia_l": al, "pd_h": key.map(daily["high"]), "pd_l": key.map(daily["low"])},
        index=idx,
    )


# ICT Silver Bullet: three one-hour windows (New York time)
SILVER_BULLET = {"Silver Bullet London": (3.0, 4.0), "Silver Bullet NY AM": (10.0, 11.0), "Silver Bullet NY PM": (14.0, 15.0)}


def silver_bullet_now(index: pd.DatetimeIndex):
    h = float(_hours(index)[-1])
    return next((n for n, (a, b) in SILVER_BULLET.items() if a <= h < b), None)
