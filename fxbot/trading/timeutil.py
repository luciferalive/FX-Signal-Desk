"""Timezone-aware sessions, trading day and week, rollover. Daylight saving is handled by zoneinfo, not by hand."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

# Each session is local wall-clock time in its own city, so London and New York move with their own daylight saving.
SESSION_DEFS = {
    "Asian": ("Asia/Tokyo", time(9, 0), time(18, 0)),
    "London": ("Europe/London", time(8, 0), time(16, 30)),
    "New York": ("America/New_York", time(8, 0), time(17, 0)),
}


def _utc(now) -> pd.Timestamp:
    t = pd.Timestamp(now)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def session_window(name: str, day_utc: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The UTC start and end of a session whose local day contains day_utc."""
    tz, a, b = SESSION_DEFS[name]
    z = ZoneInfo(tz)
    local = day_utc.tz_convert(z)
    d = local.date()
    s = pd.Timestamp(datetime.combine(d, a), tz=z).tz_convert("UTC")
    e = pd.Timestamp(datetime.combine(d, b), tz=z).tz_convert("UTC")
    return s, e


def active_sessions(now) -> list[str]:
    """Names of the sessions open right now, plus 'overlap' when London and New York are both open."""
    t = _utc(now)
    out = []
    for name in SESSION_DEFS:
        s, e = session_window(name, t)
        if s <= t < e:
            out.append(name)
    if "London" in out and "New York" in out:
        out.append("overlap")
    return out


def market_open(now, tz: str = "America/New_York", rollover_hour: int = 17) -> bool:
    """Spot forex trades from Sunday to Friday at the rollover hour (New York). Closed on the weekend."""
    t = _utc(now).tz_convert(ZoneInfo(tz))
    wd, h = t.weekday(), t.hour
    if wd == 5:
        return False
    if wd == 4 and h >= rollover_hour:
        return False
    if wd == 6 and h < rollover_hour:
        return False
    return True


def next_rollover(now, tz: str = "America/New_York", rollover_hour: int = 17) -> pd.Timestamp:
    z = ZoneInfo(tz)
    t = _utc(now).tz_convert(z)
    r = pd.Timestamp(datetime.combine(t.date(), time(rollover_hour, 0)), tz=z)
    if r <= t:
        r = pd.Timestamp(datetime.combine(t.date() + timedelta(days=1), time(rollover_hour, 0)), tz=z)
    return r.tz_convert("UTC")


def minutes_to_rollover(now, tz="America/New_York", rollover_hour=17) -> float:
    return (next_rollover(now, tz, rollover_hour) - _utc(now)).total_seconds() / 60


def minutes_to_week_close(now, tz="America/New_York", rollover_hour=17) -> float | None:
    """Minutes until the Friday close, None when the market is already closed."""
    if not market_open(now, tz, rollover_hour):
        return None
    z = ZoneInfo(tz)
    t = _utc(now).tz_convert(z)
    days = (4 - t.weekday()) % 7
    close = pd.Timestamp(datetime.combine(t.date() + timedelta(days=days), time(rollover_hour, 0)), tz=z)
    if close <= t:
        close += pd.Timedelta(days=7)
    return (close.tz_convert("UTC") - _utc(now)).total_seconds() / 60


def trading_day_key(now, tz="America/New_York", rollover_hour=17) -> str:
    """The trading day starts at the rollover hour: 17:00 Sunday begins Monday's trading day."""
    t = _utc(now).tz_convert(ZoneInfo(tz))
    if t.hour >= rollover_hour:
        t = t + pd.Timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def trading_week_key(now, tz="America/New_York", rollover_hour=17) -> str:
    """The week is labelled by the Monday of its trading week."""
    d = pd.Timestamp(trading_day_key(now, tz, rollover_hour))
    return (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")
