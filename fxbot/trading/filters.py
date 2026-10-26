"""Market-condition filters: stale data, sessions, rollover, news. Each returns a plain-words reason, or None when fine."""
from __future__ import annotations

import pandas as pd

from ..news import ccys_of
from .timeutil import active_sessions, market_open, minutes_to_rollover, minutes_to_week_close

HOLD = "REJECTED"


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def quote_stale(quote_time, now, max_age_s: float) -> str | None:
    age = (_utc(now) - _utc(quote_time)).total_seconds()
    if age > max_age_s:
        return f"{HOLD}: market data is stale (the quote is {age:.0f}s old, the limit is {max_age_s:.0f}s)."
    if age < -max_age_s:
        return f"{HOLD}: market data timestamp is in the future (clock problem)."
    return None


def current_session(now) -> str | None:
    s = active_sessions(now)
    if "overlap" in s:
        return "overlap"
    return s[0] if s else None


def session_block(now, strategy_key: str, cfg: dict) -> str | None:
    sc = cfg["sessions"]
    allowed = sc.get("strategy_sessions", {}).get(strategy_key, sc.get("allowed"))
    if not allowed:
        return None
    active = active_sessions(now)
    if not any(a in active for a in allowed):
        return f"{HOLD}: outside the allowed sessions ({', '.join(allowed)}); open now: {', '.join(active) or 'none'}."
    return None


def market_closed_block(now, cfg: dict) -> str | None:
    sc = cfg["sessions"]
    if not market_open(now, sc["broker_tz"], sc["rollover_hour"]):
        return f"{HOLD}: the forex market is closed (weekend)."
    return None


def rollover_block(now, style: str, cfg: dict) -> str | None:
    """No new trades shortly before rollover or the weekend close; intraday styles also must not enter overnight."""
    sc = cfg["sessions"]
    m = minutes_to_rollover(now, sc["broker_tz"], sc["rollover_hour"])
    if m <= sc["block_new_before_rollover_min"]:
        return f"{HOLD}: rollover is in {m:.0f} min (no new trades in the last {sc['block_new_before_rollover_min']} min)."
    w = minutes_to_week_close(now, sc["broker_tz"], sc["rollover_hour"])
    if w is not None and w <= sc["block_new_before_rollover_min"]:
        return f"{HOLD}: the weekly close is in {w:.0f} min."
    return None


def news_block(events: list[dict] | None, symbol: str, now, cfg: dict) -> str | None:
    n = cfg["news"]
    if not n["enabled"]:
        return None
    t = _utc(now).timestamp()
    cc = ccys_of(symbol)
    for e in events or []:
        if e["impact"] in n["impacts"] and e["ccy"] in cc:
            d = e["ts"] - t
            if -n["block_after_min"] * 60 <= d <= n["block_before_min"] * 60:
                when = f"in {d / 60:.0f} min" if d >= 0 else f"{-d / 60:.0f} min ago"
                return f"{HOLD}: high-impact news for {e['ccy']} ({e['title']}) {when}."
    return None


def news_close_needed(events, symbol: str, now, cfg: dict) -> str | None:
    """For OPEN trades: a reason to close only when news.existing_positions is 'close'. Default is to retain."""
    if cfg["news"]["existing_positions"] != "close":
        return None
    return news_block(events, symbol, now, cfg)
