import json
import os
import tempfile

import numpy as np
import pandas as pd

from fxbot import load_symbols
from fxbot.data import resample, split_bulk, synthetic
from fxbot.modes import TIMEFRAMES
from fxbot.news import ccys_of, parse_events, sample_news, upcoming_high


def test_pairs_file_has_all_28_forex_pairs():
    syms = load_symbols("pairs.txt")
    majors = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
    have = set(syms)
    pairs = {frozenset((s[:3], s[3:])) for s in have if len(s) == 6 and not s.startswith("X")}
    need = {frozenset((a, b)) for i, a in enumerate(majors) for b in majors[i + 1:]}
    assert need <= pairs, sorted(tuple(sorted(x)) for x in need - pairs)
    assert "XAUUSD" in have and "XAGUSD" in have
    assert len(have) == len(syms)  # no duplicates


def test_news_parsing_and_colours():
    rows = [
        {"title": "CPI", "country": "usd", "date": "2026-10-20T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
        {"title": "Speech", "country": "GBP", "date": "2026-10-20T10:00:00-04:00", "impact": "Low", "forecast": "", "previous": ""},
        {"title": "Broken", "country": "EUR", "date": "not a date", "impact": "High"},
        {"title": "Mystery", "country": "EUR", "date": "2026-10-20T10:00:00-04:00", "impact": "???"},
    ]
    ev = parse_events(rows)
    assert [e["title"] for e in ev] == ["CPI", "Speech"]
    assert ev[0]["ccy"] == "USD" and ev[0]["impact"] == "high"
    assert ev[0]["ts"] == int(pd.Timestamp("2026-10-20T12:30:00Z").timestamp())  # -04:00 converted to UTC


def test_news_warning_only_for_the_pairs_currencies():
    now = pd.Timestamp("2026-10-20 12:00", tz="UTC")
    ev = [dict(ts=int((now + pd.Timedelta(minutes=40)).timestamp()), ccy="EUR", impact="high", title="German CPI", forecast="", previous="")]
    assert "EUR German CPI" in upcoming_high(ev, "EURUSD", now)
    assert upcoming_high(ev, "GBPJPY", now) is None
    assert upcoming_high(ev, "XAUUSD", now) is None  # gold is priced in USD only
    assert ccys_of("XAUUSD") == ["USD"] and ccys_of("GBPJPY") == ["GBP", "JPY"]
    ev[0]["ts"] = int((now + pd.Timedelta(hours=5)).timestamp())
    assert upcoming_high(ev, "EURUSD", now) is None  # too far away to matter


def test_bulk_split_handles_both_column_orders():
    idx = pd.date_range("2026-01-05", periods=4, freq="h", tz="UTC")
    cols = ["Open", "High", "Low", "Close", "Volume"]
    a = pd.DataFrame(np.arange(20.0).reshape(4, 5) + 1, index=idx, columns=cols)
    b = a + 10
    by_ticker = pd.concat({"EURUSD=X": a, "GBPUSD=X": b}, axis=1)            # group_by="ticker": (ticker, field)
    by_field = by_ticker.swaplevel(0, 1, axis=1).sort_index(axis=1)           # (field, ticker)
    for raw in (by_ticker, by_field):
        out = split_bulk(raw, ["EURUSD=X", "GBPUSD=X", "MISSING=X"])
        assert set(out) == {"EURUSD=X", "GBPUSD=X"}
        assert list(out["EURUSD=X"].columns) == ["open", "high", "low", "close", "volume"]
        assert float(out["GBPUSD=X"]["close"].iloc[0]) == float(b["Close"].iloc[0])


def test_weekly_and_monthly_resample():
    df = synthetic("1h", days=400)
    w, m = resample(df, "1w"), resample(df, "1M")
    assert 50 <= len(w) <= 70 and 12 <= len(m) <= 16
    assert (w["high"] >= w["low"]).all() and (m["high"] >= m["low"]).all()
    assert abs(float(m["high"].max()) - float(df["high"].max())) < 1e-12


def test_demo_dashboard_builds_with_all_timeframes_and_news():
    from fxbot.dashboard import build, demo_frames
    with tempfile.TemporaryDirectory() as d:
        path = build(["EURUSD", "USDJPY"], d, frames=demo_frames(["EURUSD", "USDJPY"]), news=sample_news(), demo=True, inline=True)
        html = open(path, encoding="utf-8").read()
        assert "__INLINE__" not in html and "FX Signal Desk" in html
        assert "EURUSD" in html and "Non-Farm Payrolls" in html
        for tf in TIMEFRAMES:
            assert f'"{tf}"' in html, tf
    with tempfile.TemporaryDirectory() as d:  # live layout: small json files next to the page
        build(["EURUSD"], d, frames=demo_frames(["EURUSD"]), news=sample_news(), demo=True)
        idx = json.load(open(os.path.join(d, "data", "index.json")))
        assert [p["symbol"] for p in idx["pairs"]] == ["EURUSD"] and len(idx["tfs"]) == 9
        pair = json.load(open(os.path.join(d, "data", "EURUSD.json")))
        assert set(pair["tfs"]) == set(TIMEFRAMES)
        assert json.load(open(os.path.join(d, "data", "news.json")))["ok"] is True
