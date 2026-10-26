"""News trading playbook: classification, the IF-THEN map, outcome from a real number, reaction maths, and that it never trades."""
import os

import pandas as pd

from fxbot import newsplay as n
from fxbot.news import sample_news

NFP = dict(title="Non-Farm Payrolls", ccy="USD", forecast="165K", previous="142K", impact="high", ts=0)


def test_parse_num():
    assert n.parse_num("165K") == 165000 and n.parse_num("-0.2%") == -0.2 and n.parse_num("4.25%") == 4.25
    assert n.parse_num("1.2M") == 1.2e6 and n.parse_num("") is None and n.parse_num("abc") is None and n.parse_num(None) is None


def test_classify():
    for title, kind, sign in [("Non-Farm Payrolls", "jobs", 1), ("Unemployment Rate", "unemployment", -1), ("Unemployment Claims", "claims", -1),
                              ("German CPI m/m", "inflation", 1), ("Official Bank Rate", "rates", 1), ("FOMC Press Conference", "words", 0),
                              ("Core Retail Sales m/m", "retail", 1), ("Prelim GDP q/q", "gdp", 1), ("Something odd", "other", 0)]:
        k = n.classify(title)
        assert (k["kind"], k["sign"]) == (kind, sign), title


def test_pair_side():
    assert n.pair_side("USDJPY", "USD", 1) == "BUY" and n.pair_side("USDJPY", "USD", -1) == "SELL"
    assert n.pair_side("EURUSD", "USD", 1) == "SELL" and n.pair_side("EURUSD", "USD", -1) == "BUY"
    assert n.pair_side("XAUUSD", "USD", 1) == "SELL"          # gold is priced in USD
    assert n.pair_side("EURJPY", "USD", 1) is None


def test_scenarios_jobs_and_inverse():
    rows = {r["symbol"]: r for r in n.scenarios(NFP, ["USDJPY", "EURUSD", "EURJPY"])}
    assert "EURJPY" not in rows
    assert (rows["USDJPY"]["up"], rows["USDJPY"]["down"]) == ("BUY", "SELL")
    assert (rows["EURUSD"]["up"], rows["EURUSD"]["down"]) == ("SELL", "BUY")
    unemp = dict(NFP, title="Unemployment Rate")            # a higher number is bad for USD
    r = n.scenarios(unemp, ["USDJPY"])[0]
    assert (r["up"], r["down"]) == ("SELL", "BUY")
    words = n.scenarios(dict(NFP, title="FOMC Press Conference"), ["USDJPY"])[0]
    assert (words["up_label"], words["up"], words["down"]) == ("Hawkish tone", "BUY", "SELL")


def test_outcome():
    o = n.outcome(NFP, "210K"); assert o["result"] == "above" and o["ccy_dir"] == 1
    o = n.outcome(NFP, "120K"); assert o["result"] == "below" and o["ccy_dir"] == -1
    assert n.outcome(NFP, "165K")["result"] == "in line"
    assert n.outcome(dict(NFP, title="Unemployment Rate", forecast="4.0%"), "4.4%")["ccy_dir"] == -1   # higher unemployment = weaker USD
    assert n.outcome(NFP, "x") is None and n.outcome(dict(NFP, forecast=""), "1") is None
    assert n.outcome(dict(NFP, title="FOMC Press Conference"), "1") is None


def _candles(start, step, closes, open0):
    out, o = [], open0
    for i, c in enumerate(closes):
        out.append([start + i * step, o, max(o, c) + 0.0002, min(o, c) - 0.0002, c, "x"]); o = c
    return out


def test_reaction_with_offset():
    off, ev = 19800, 1_700_000_000
    first = (ev // 900) * 900                                # the 15m candle that holds the release
    c = _candles(first + off - 6 * 900, 900, [1.1000] * 6 + [1.0990, 1.0985, 1.0980, 1.0975, 1.0970, 1.0965], 1.1000)
    r = n.reaction(c, ev, 5, off)
    assert r["first"]["dir"] == "down" and r["first"]["pips"] < 0 and r["m30"]["dir"] == "down" and r["m60"]["dir"] == "down"
    assert n.reaction(c, ev + 10 ** 7, 5, off) is None and n.reaction([], ev, 5) is None


def _now():
    return pd.Timestamp("2026-10-26 08:00", tz="UTC")


def _ev(title, ccy, hours, forecast="165K", previous="142K", actual="", impact="high"):
    return dict(title=title, ccy=ccy, impact=impact, ts=int((_now() + pd.Timedelta(hours=hours)).timestamp()), forecast=forecast, previous=previous, actual=actual)


def test_verdict_lean_and_reasons():
    up = dict(bias="BUY", trend="higher highs", chg=0.4, strength={"EUR": 0.2, "USD": -0.1}, sig={"15m": dict(side="BUY", grade="A")})
    v = n.verdict("EURUSD", up, [], None, _now())
    assert v["lean"] == "BUY" and v["score"] >= 60 and v["confidence"] == "Medium" and v["reasons"]
    dn = dict(bias="SELL", chg=-0.4, strength={"EUR": -0.2, "USD": 0.1}, sig={})
    assert n.verdict("EURUSD", dn, [], None, _now())["lean"] == "SELL"
    mixed = dict(bias="BUY", chg=-0.3, strength={"EUR": -0.2, "USD": 0.1}, sig={})
    assert n.verdict("EURUSD", mixed, [], None, _now())["lean"] == "WAIT"
    assert n.verdict("EURUSD", dict(bias="NEUTRAL"), [], None, _now())["lean"] == "WAIT"


def test_verdict_gold_uses_dollar():
    weak_usd = dict(bias="BUY", chg=0.3, strength={"USD": -0.3})
    v = n.verdict("XAUUSD", weak_usd, [], None, _now())
    assert v["lean"] == "BUY" and any("dollar" in r["text"] for r in v["reasons"])
    strong_usd = dict(bias="SELL", chg=-0.3, strength={"USD": 0.3})
    assert n.verdict("XAUUSD", strong_usd, [], None, _now())["lean"] == "SELL"


def test_verdict_waits_before_big_news_then_uses_the_number():
    info = dict(bias="BUY", chg=0.4, strength={"EUR": 0.2, "USD": -0.1}, sig={"15m": dict(side="BUY", grade="A")})
    soon = [_ev("Non-Farm Payrolls", "USD", 0.25)]
    v = n.verdict("EURUSD", info, soon, None, _now())
    assert v["lean"] == "WAIT" and "do not enter" in v["note"] and "BUY" in v["note"]
    just = [_ev("Non-Farm Payrolls", "USD", -0.1)]
    assert "first candle" in n.verdict("EURUSD", info, just, None, _now())["note"]
    # the number is out and strong: USD up -> SELL EURUSD outweighs a weak BUY
    weak = dict(bias="NEUTRAL", chg=0.0, strength={}, sig={})
    out = [_ev("Non-Farm Payrolls", "USD", -1, actual="250K")]
    v2 = n.verdict("EURUSD", weak, out, None, _now())
    assert v2["lean"] == "SELL" and any("came out above" in r["text"] for r in v2["reasons"]) and v2["note"] is None
    # gold: USD up -> SELL
    assert n.verdict("XAUUSD", dict(bias="NEUTRAL"), out, None, _now())["lean"] == "SELL"


def test_headline_tilt():
    heads = [dict(title="EUR/USD rises toward 1.13 as dollar softens"), dict(title="EUR/USD climbs after ECB"), dict(title="Gold falls as yields jump"),
             dict(title="EUR/USD flat ahead of data")]
    pts, hit = n.headline_tilt("EURUSD", heads)
    assert pts == 6 and len(hit) == 2 and all(h["mood"] == "up" for h in hit)
    assert n.headline_tilt("XAUUSD", heads)[0] == -3
    assert n.headline_tilt("EURUSD", [])[0] == 0 and n.headline_tilt("EURUSD", None)[0] == 0


def test_parse_rss_and_actual_field():
    from fxbot.news import parse_rss, parse_events
    xml = ("<rss><channel><item><title>Gold extends gains</title><link>http://x/1</link><pubDate>Mon, 26 Oct 2026 08:00:00 GMT</pubDate></item>"
           "<item><title></title></item></channel></rss>")
    r = parse_rss(xml, "T")
    assert len(r) == 1 and r[0]["title"] == "Gold extends gains" and r[0]["ts"] > 0 and parse_rss("<<not xml", "T") == []
    ev = parse_events([dict(date="2026-10-26T08:30:00-04:00", country="usd", impact="High", title="X", forecast="1", previous="2", actual="3")])
    assert ev[0]["actual"] == "3"


def test_build_blocks():
    now = _now()
    pairs = [dict(symbol="EURUSD", dec=5, price=1.1, chg=0.2, sig={}), dict(symbol="XAUUSD", dec=2, price=3000.0, chg=-0.1, sig={}),
             dict(symbol="GBPJPY", dec=3, price=200.0, chg=0.0, sig={})]
    evs = [_ev("Non-Farm Payrolls", "USD", 5), _ev("German CPI m/m", "EUR", 3, "0.2%", "0.1%"), _ev("BOE Member Speech", "GBP", 2, impact="low"),
           _ev("Official Bank Rate", "GBP", 4, "4.0%", "4.25%")]
    b = n.build(evs, pairs, None, now, infos={"EURUSD": dict(bias="BUY")}, headlines=[dict(title="Gold rises", source="S", ts=0, url="")])
    assert b["auto_trade"] is False and b["cautions"] and [x["symbol"] for x in b["symbols"]] == ["EURUSD", "XAUUSD"]
    eu, au = b["symbols"]
    assert [e["title"] for e in eu["events"]] == ["German CPI m/m", "Non-Farm Payrolls"]       # EUR + USD only, soonest first, no GBP / low impact
    assert [e["title"] for e in au["events"]] == ["Non-Farm Payrolls"]
    nfp = au["events"][0]; assert len(nfp["scenarios"]) == 1 and nfp["scenarios"][0]["up"] == "SELL" and "better" in nfp["expect"]
    assert au["headlines"] and eu["verdict"]["lean"] in ("BUY", "WAIT", "SELL")


def test_dashboard_switch():
    from fxbot.dashboard import build, demo_frames
    import json, re, tempfile
    for on in (True, False):
        out = tempfile.mkdtemp()
        cfg = __import__("fxbot.config", fromlist=["load"]).load("/nonexistent"); cfg["news_trading"]["enabled"] = on
        path = build(["EURUSD", "XAUUSD"], out, frames=demo_frames(["EURUSD", "XAUUSD"]), news=sample_news(), demo=True, inline=True, cfg=cfg)
        blob = json.loads(re.search(r"const INLINE = (\{.*?\});   /\*", open(path, encoding="utf-8").read(), re.S).group(1).replace("<\\/", "</"))
        assert (blob["index"]["newsplay"] is not None) == on
        if on:
            assert [x["symbol"] for x in blob["index"]["newsplay"]["symbols"]] == ["EURUSD", "XAUUSD"]


def test_never_trades():
    src = open(n.__file__, encoding="utf-8").read()
    assert not [l for l in src.splitlines() if l.startswith(("import", "from")) and ("trading" in l or "broker" in l)]
    for fn in os.listdir(os.path.join(os.path.dirname(n.__file__), "trading")):
        if fn.endswith(".py"):
            assert "newsplay" not in open(os.path.join(os.path.dirname(n.__file__), "trading", fn), encoding="utf-8").read()
