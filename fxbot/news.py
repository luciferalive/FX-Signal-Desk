"""Economic calendar (fundamental news) for the dashboard.

Source: the free weekly calendar JSON that Forex Factory publishes (no key needed). It is downloaded while the
dashboard is built (on GitHub, not in your browser), so there are no browser restrictions. If the download fails
the dashboard says so and falls back to TradingView's own calendar widget.

Impact colours on the page: High = red, Medium = yellow, Low = green.
"""
from __future__ import annotations

import json
import time
import urllib.request

import pandas as pd

FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",  # only exists later in the week, a miss is fine
]
IMPACT = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday", "non-economic": "low"}


def parse_events(rows: list[dict]) -> list[dict]:
    """Normalise feed rows: UTC epoch seconds, lower-case impact, upper-case currency."""
    out = []
    for r in rows or []:
        try:
            ts = int(pd.Timestamp(r["date"]).tz_convert("UTC").timestamp())
        except Exception:
            continue
        impact = IMPACT.get(str(r.get("impact", "")).strip().lower())
        if impact is None:
            continue
        out.append(dict(
            ts=ts, ccy=str(r.get("country", "")).strip().upper(), impact=impact,
            title=str(r.get("title", "")).strip(), forecast=str(r.get("forecast", "") or "").strip(),
            previous=str(r.get("previous", "") or "").strip(),
        ))
    out.sort(key=lambda e: e["ts"])
    return out


def _get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; fxbot/0.4)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_news() -> dict:
    """Never raises. Returns {ok, fetched, events, error}."""
    events: list[dict] = []
    errors: list[str] = []
    for i, url in enumerate(FEEDS):
        for attempt in range(2):
            try:
                events += parse_events(_get(url))
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                if i == 0:  # a missing next-week file is normal, a missing this-week file is not
                    errors.append(f"{type(e).__name__}: {e}")
    events.sort(key=lambda e: e["ts"])
    seen, uniq = set(), []
    for e in events:
        k = (e["ts"], e["ccy"], e["title"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return dict(ok=bool(uniq), fetched=pd.Timestamp.now(tz="UTC").isoformat(), events=uniq,
                error="; ".join(errors) if not uniq else None)


def sample_news(now: pd.Timestamp | None = None) -> dict:
    """Clearly fake events for the offline demo, so the colours and countdowns can be seen."""
    now = now or pd.Timestamp.now(tz="UTC")
    rows = [
        (-6, "USD", "high", "Core Retail Sales m/m", "0.3%", "0.1%"), (-3, "GBP", "low", "BOE Member Speech", "", ""),
        (0.75, "EUR", "high", "German CPI m/m", "0.2%", "0.1%"), (2, "USD", "medium", "Unemployment Claims", "231K", "228K"),
        (5, "JPY", "low", "Tokyo Core CPI y/y", "2.4%", "2.5%"), (9, "GBP", "high", "Official Bank Rate", "4.00%", "4.25%"),
        (14, "AUD", "medium", "Employment Change", "21K", "42K"), (20, "CAD", "medium", "Retail Sales m/m", "0.4%", "0.3%"),
        (27, "USD", "high", "Non-Farm Payrolls", "165K", "142K"), (31, "EUR", "low", "Final CPI y/y", "2.1%", "2.1%"),
        (50, "CHF", "low", "KOF Economic Barometer", "101.2", "100.8"),
    ]
    ev = [dict(ts=int((now + pd.Timedelta(hours=h)).timestamp()), ccy=c, impact=i, title=t, forecast=f, previous=p)
          for h, c, i, t, f, p in rows]
    return dict(ok=True, fetched=now.isoformat(), events=ev, error=None, sample=True)


def ccys_of(symbol: str) -> list[str]:
    """Currencies that move a pair: EURUSD -> EUR, USD. Gold and silver are priced in USD."""
    s = symbol.upper().replace("/", "")
    base, quote = s[:3], s[3:6]
    return [c for c in (base, quote) if c not in ("XAU", "XAG")] or ["USD"]


def upcoming_high(events: list[dict], symbol: str, now: pd.Timestamp | None = None, before_min: int = 90,
                  after_min: int = 15) -> str | None:
    """A one-line warning when a high-impact event for the pair's currencies is close, else None."""
    now = now or pd.Timestamp.now(tz="UTC")
    t = now.timestamp()
    cc = ccys_of(symbol)
    hot = [e for e in events if e["impact"] == "high" and e["ccy"] in cc and -after_min * 60 <= e["ts"] - t <= before_min * 60]
    if not hot:
        return None
    e = min(hot, key=lambda x: abs(x["ts"] - t))
    mins = round((e["ts"] - t) / 60)
    when = f"in {mins} min" if mins >= 0 else f"{-mins} min ago"
    return f"WARNING high-impact news: {e['ccy']} {e['title']} {when}. Avoid new trades until it has passed."
