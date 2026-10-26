"""News trading playbook (information only: this module never places an order and never talks to a broker).

For each big scheduled event it answers, in plain words:
  * what the event measures and which way a surprise usually pushes the currency,
  * "what if" scenarios for every affected pair (number above / below / equal to the forecast -> BUY or SELL),
  * for events that already happened, what the pair actually did in the next 15 / 30 / 60 minutes (from the candles).

Honest limits: nobody knows the number before it is released, so before the release the playbook only gives the
IF-THEN map, never a prediction. The common rule "currency strengthens when the data beats the forecast" is a
rule of thumb; markets also react to the size of the surprise, revisions, the tone of statements and what was
already priced in, and the first move is often reversed. Spreads widen and fills slip around news.
"""
from __future__ import annotations

import re

import pandas as pd

from .news import ccys_of

# (regex on the lower-case title, key, label, sign, plain explanation). sign = +1: a higher number is good for the
# currency, -1: a higher number is bad for it, 0: not a number (a statement or tone). First match wins.
KINDS = [
    (r"unemployment rate|jobless rate", "unemployment", "Unemployment rate", -1,
     "Share of people without a job. Higher than expected means a weaker economy, usually a weaker currency."),
    (r"unemployment claims|jobless claims|initial claims|continuing claims", "claims", "Jobless claims", -1,
     "Weekly count of new unemployment claims. More claims than expected usually weakens the currency."),
    (r"non-?farm|nfp|employment change|payroll|\badp\b|\bemployment\b|\bjobs\b", "jobs", "Jobs report", 1,
     "Counts new jobs. More jobs than expected means a stronger economy and usually a stronger currency."),
    (r"hourly earnings|wage|average earnings|labou?r cost", "wages", "Wages", 1,
     "Pay growth feeds inflation. Higher than expected can raise rate-hike hopes and lift the currency."),
    (r"cpi|inflation|ppi|pce|price index|trimmed mean|deflator|hicp", "inflation", "Inflation", 1,
     "Price growth. Higher than expected makes rate hikes more likely, which usually lifts the currency short term."),
    (r"rate decision|interest rate|bank rate|cash rate|official cash|refinancing|policy rate|funds rate|rate statement|monetary policy|\bocr\b", "rates", "Interest rate decision", 1,
     "The central bank's rate. A higher rate or a more hawkish message than expected usually lifts the currency."),
    (r"\bgdp\b|gross domestic", "gdp", "Growth (GDP)", 1,
     "Size of the economy. Growth above the forecast usually strengthens the currency."),
    (r"retail sales", "retail", "Retail sales", 1, "Shop spending. Higher than expected usually strengthens the currency."),
    (r"\bpmi\b|\bism\b|\bifo\b|\bzew\b|tankan|business climate|business confidence", "survey", "Business survey", 1,
     "Survey of company managers. Above the forecast usually strengthens the currency."),
    (r"trade balance|current account|exports|imports", "trade", "Trade", 1,
     "Exports minus imports. A better balance than expected usually strengthens the currency."),
    (r"industrial|manufacturing|factory|durable|housing|building permits|home sales|consumer confidence|sentiment|confidence", "activity", "Activity data", 1,
     "A smaller activity report. Above the forecast usually helps the currency a little."),
    (r"speaks|speech|testifies|press conference|minutes|statement|meeting|testimony", "words", "Statement or speech", 0,
     "Words, not numbers. A hawkish tone (more hikes, fewer cuts) lifts the currency, a dovish tone weakens it. The direction is only known after you read it."),
]
UNKNOWN = ("other", "Economic data", 0, "Direction depends on how the number compares with the forecast.")

CAUTIONS = [
    "Prices can jump, spreads widen and stops can slip around the release. The planned risk is not guaranteed.",
    "Many traders wait for the first candle after the release to close before acting, because the first spike often reverses.",
    "Do not enter in the 5 minutes before the release. Use a smaller size than normal, or skip it.",
    "The rule of thumb (better data = stronger currency) fails often when the market had already priced the news in.",
    "This desk shows the map only. It never places news trades for you.",
]


def classify(title: str) -> dict:
    t = (title or "").lower()
    for pat, key, label, sign, note in KINDS:
        if re.search(pat, t):
            return dict(kind=key, label=label, sign=sign, note=note)
    key, label, sign, note = UNKNOWN
    return dict(kind=key, label=label, sign=sign, note=note)


_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_num(s) -> float | None:
    """'0.3%' -> 0.3, '165K' -> 165000, '-5.4B' -> -5.4e9, '4.25%' -> 4.25, '' -> None. Ranges like '1.5|2.0' use the first value."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s:
        return None
    m = re.match(r"^([-+]?\d*\.?\d+)\s*([kmbt])?%?", s.lower())
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v * _MULT.get(m.group(2) or "", 1.0)


def pair_side(symbol: str, ccy: str, ccy_dir: int) -> str | None:
    """BUY or SELL on `symbol` if currency `ccy` rises (ccy_dir=+1) or falls (-1). None if the pair does not contain it.
    Gold and silver are priced in USD, so a stronger USD means SELL."""
    s = symbol.upper().replace("/", "")
    base, quote = s[:3], s[3:6]
    if ccy == base:
        return "BUY" if ccy_dir > 0 else "SELL"
    if ccy == quote:
        return "SELL" if ccy_dir > 0 else "BUY"
    return None


def scenarios(ev: dict, symbols: list[str]) -> list[dict]:
    """The IF-THEN map for one event: one row per affected pair."""
    k = classify(ev["title"])
    rows = []
    for sym in symbols:
        up, dn = pair_side(sym, ev["ccy"], 1), pair_side(sym, ev["ccy"], -1)
        if not up:
            continue
        if k["sign"] == 0:
            rows.append(dict(symbol=sym, up_label="Hawkish tone", up=up, down_label="Dovish tone", down=dn, equal=None))
        else:
            # sign=+1: a higher number lifts the currency. sign=-1: a higher number weakens it.
            hi, lo = (up, dn) if k["sign"] > 0 else (dn, up)
            rows.append(dict(symbol=sym, up_label="Above forecast", up=hi, down_label="Below forecast", down=lo, equal="Wait"))
    return rows


def outcome(ev: dict, actual) -> dict | None:
    """Given the real number, which way does the rule of thumb point? None if the number or forecast cannot be read."""
    k = classify(ev["title"])
    a, f = parse_num(actual), parse_num(ev.get("forecast"))
    if a is None or f is None or k["sign"] == 0:
        return None
    if abs(a - f) <= 1e-12 * max(1.0, abs(f)):
        return dict(result="in line", ccy_dir=0, text=f"{ev['ccy']} number is in line with the forecast, so there is no clear push. Wait for the next candles.")
    above = a > f
    ccy_dir = (1 if above else -1) * k["sign"]
    word = "above" if above else "below"
    move = "stronger" if ccy_dir > 0 else "weaker"
    return dict(result=word, ccy_dir=ccy_dir,
                text=f"Number is {word} the forecast ({ev.get('forecast')}), which usually points to a {move} {ev['ccy']}.")


def reaction(candles: list, ev_ts: int, dec: int, t_offset: int = 0) -> dict | None:
    """What the pair did after the release, from 15-minute candles [t, o, h, l, c, session].
    Candle times may be shifted by `t_offset` seconds (the dashboard uses IST). None when the candles do not cover the event."""
    if not candles or len(candles) < 6:
        return None
    ts = ev_ts + t_offset
    step = candles[1][0] - candles[0][0]
    idx = next((i for i, c in enumerate(candles) if c[0] <= ts < c[0] + step), None)
    if idx is None or idx + 1 >= len(candles):
        return None
    c0 = candles[idx]
    out = dict(first=dict(dir="up" if c0[4] > c0[1] else "down" if c0[4] < c0[1] else "flat",
                          pct=round((c0[4] / c0[1] - 1) * 100, 3), range_pct=round((c0[2] - c0[3]) / c0[1] * 100, 3)))
    pip = 10 ** -(dec - 1) if dec in (3, 5) else None
    if pip:
        out["first"]["pips"] = round((c0[4] - c0[1]) / pip, 1)
    for label, n in (("m30", 1), ("m60", 3)):
        j = idx + n
        if j < len(candles):
            move = candles[j][4] / c0[1] - 1
            out[label] = dict(pct=round(move * 100, 3), dir="up" if move > 0 else "down" if move < 0 else "flat")
            if pip:
                out[label]["pips"] = round((candles[j][4] - c0[1]) / pip, 1)
    return out


UP_WORDS = ("rises", "rise", "gains", "gain", "climbs", "climb", "jumps", "rallies", "rally", "higher", "advances", "extends gains", "firmer", "surges", "recovers", "rebounds")
DOWN_WORDS = ("falls", "fall", "drops", "drop", "slides", "slips", "slumps", "lower", "retreats", "declines", "dips", "weakens", "sinks", "tumbles", "pressured", "extends losses")
NAMES = {"EURUSD": ("eur/usd", "eurusd", "euro "), "XAUUSD": ("xau/usd", "xauusd", "gold"), "XAGUSD": ("xag/usd", "silver")}


def headline_tilt(symbol: str, headlines: list[dict] | None, max_pts: int = 10) -> tuple[int, list[dict]]:
    """Small push from headlines that name the instrument and say it is rising or falling. Momentum, mostly already priced in."""
    names = NAMES.get(symbol.upper(), (symbol.lower(),))
    hit, net = [], 0
    for h in headlines or []:
        t = " " + str(h.get("title", "")).lower() + " "
        if not any(n in t for n in names):
            continue
        up, dn = any(f" {w} " in t or f" {w}," in t or f" {w}." in t for w in UP_WORDS), any(f" {w} " in t or f" {w}," in t or f" {w}." in t for w in DOWN_WORDS)
        if up == dn:
            continue
        net += 1 if up else -1
        hit.append(dict(title=h.get("title", ""), mood="up" if up else "down"))
    pts = max(-max_pts, min(max_pts, net * 3))
    return pts, hit[:4]


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def verdict(symbol: str, info: dict, events: list[dict], headlines: list[dict] | None = None, now: pd.Timestamp | None = None) -> dict:
    """One lean for one instrument: BUY, SELL or WAIT, with the points behind it. NOT a prediction and not advice.
    info: bias ('BUY'/'SELL'/'NEUTRAL'), trend (text), chg (24h %), strength ({ccy: %}), sig ({tf: {side, grade}}).
    Points: 1h trend +-30, 24h move +-10, currency strength +-15, signal engine +-8..20, expected result of upcoming news +-5 each,
    result of a released event (when the feed has the number) +-40, headline mood +-10. Lean needs 25 points either way."""
    now = now or pd.Timestamp.now(tz="UTC")
    t = now.timestamp()
    reasons, score = [], 0

    def add(p, text):
        nonlocal score
        p = int(round(p))
        score += p
        reasons.append(dict(pts=p, text=text))

    bias = info.get("bias")
    if bias in ("BUY", "SELL"):
        add(30 if bias == "BUY" else -30, f"The 1h trend is {'up' if bias == 'BUY' else 'down'}" + (f" ({info['trend']})" if info.get("trend") else ""))
    else:
        reasons.append(dict(pts=0, text="The 1h trend is unclear (no side)"))
    chg = info.get("chg") or 0
    if abs(chg) >= 0.05:
        add(_clip(chg / 0.4, -1, 1) * 10, f"{symbol} moved {chg:+.2f}% in the last 24 hours")
    st = info.get("strength") or {}
    base, quote = symbol[:3], symbol[3:6]
    d = -st.get("USD", 0) if base in ("XAU", "XAG") else st.get(base, 0) - st.get(quote, 0)
    if abs(d) >= 0.03 and st:
        txt = (f"The US dollar has been {'weaker' if d > 0 else 'stronger'} lately, which usually {'helps' if d > 0 else 'hurts'} gold" if base in ("XAU", "XAG")
               else f"{base} has been {'stronger' if d > 0 else 'weaker'} than {quote} over 24 hours")
        add(_clip(d / 0.3, -1, 1) * 15, txt)
    best = None
    for tf, x in (info.get("sig") or {}).items():
        w = {"A": 20, "B": 15, "C": 8}.get(x.get("grade"), 8)
        if best is None or w > best[0]:
            best = (w, x, tf)
    if best:
        add(best[0] if best[1]["side"] == "BUY" else -best[0], f"The signal engine has a {best[1]['side']} (grade {best[1].get('grade', '?')}, {best[2]})")
    rel = [e for e in events if e["impact"] == "high" and e["ccy"] in ccys_of(symbol)]
    imminent = None
    for e in rel:
        k, dt = classify(e["title"]), e["ts"] - t
        if -900 <= dt <= 1800 and not parse_num(e.get("actual")):
            imminent = e if imminent is None or abs(dt) < abs(imminent["ts"] - t) else imminent
        if k["sign"] == 0:
            continue
        a, f, p = parse_num(e.get("actual")), parse_num(e.get("forecast")), parse_num(e.get("previous"))
        if a is not None and f is not None and -6 * 3600 <= dt <= 0 and a != f:
            side = pair_side(symbol, e["ccy"], (1 if a > f else -1) * k["sign"])
            if side:
                add(40 if side == "BUY" else -40, f"{e['ccy']} {e['title']} came out {'above' if a > f else 'below'} the forecast ({e.get('actual')} vs {e.get('forecast')})")
        elif 0 < dt <= 24 * 3600 and f is not None and p is not None and f != p:
            side = pair_side(symbol, e["ccy"], (1 if f > p else -1) * k["sign"])
            if side:
                add(5 if side == "BUY" else -5, f"{e['ccy']} {e['title']} is due: the forecast ({e.get('forecast')}) is {'above' if f > p else 'below'} last time ({e.get('previous')}), so the market already expects it (small weight)")
    hp, hits = headline_tilt(symbol, headlines)
    if hp:
        add(hp, f"Headlines mostly say {symbol} is {'rising' if hp > 0 else 'falling'} (small weight, already in the price)")
    score = _clip(score, -100, 100)
    lean = "BUY" if score >= 25 else "SELL" if score <= -25 else "WAIT"
    conf = "-" if lean == "WAIT" else ("Medium" if abs(score) >= 60 else "Low")
    note = None
    if imminent is not None:
        dt = imminent["ts"] - t
        note = (f"Big news {imminent['ccy']} {imminent['title']} in {round(dt / 60)} min: do not enter before the number." if dt > 0
                else f"{imminent['ccy']} {imminent['title']} was just released: wait for the first candle to close, then read the number.")
        if lean != "WAIT":
            note += f" Without the news the points say {lean}."
        lean, conf = "WAIT", "-"
    return dict(lean=lean, score=int(score), confidence=conf, reasons=reasons, note=note, headline_hits=hits)


def build(events: list[dict], pairs: list[dict], candles: dict | None = None, now: pd.Timestamp | None = None,
          hours_ahead: int = 96, hours_back: int = 30, t_offset: int = 0, symbols: list[str] | None = None,
          infos: dict | None = None, headlines: list[dict] | None = None, strength: dict | None = None) -> dict:
    """Playbook for the dashboard: one block per instrument (default EURUSD and XAUUSD) with a lean, the reasons,
    the news that moves it, and headlines. `pairs` is the index pair list (symbol, dec, price, chg, sig)."""
    now = now or pd.Timestamp.now(tz="UTC")
    t = now.timestamp()
    by = {p["symbol"]: p for p in pairs}
    blocks = []
    for sym in symbols or ["EURUSD", "XAUUSD"]:
        p = by.get(sym)
        if not p:
            continue
        info = dict(infos.get(sym, {}) if infos else {}, chg=p.get("chg"), sig=p.get("sig"), strength=strength)
        cc = ccys_of(sym)
        cards = []
        for e in events:
            if e["impact"] != "high" or e["ccy"] not in cc or not (t - hours_back * 3600 <= e["ts"] <= t + hours_ahead * 3600):
                continue
            k = classify(e["title"])
            card = dict(ts=e["ts"], ccy=e["ccy"], title=e["title"], forecast=e.get("forecast", ""), previous=e.get("previous", ""),
                        actual=e.get("actual", ""), kind=k["kind"], label=k["label"], sign=k["sign"], note=k["note"], past=e["ts"] < t,
                        scenarios=scenarios(e, [sym]))
            f, pv = parse_num(e.get("forecast")), parse_num(e.get("previous"))
            if f is not None and pv is not None and k["sign"] != 0 and f != pv:
                better = (f > pv) == (k["sign"] > 0)
                card["expect"] = ("Forecast is better than last time, so some good news is already expected." if better
                                  else "Forecast is weaker than last time, so some bad news is already expected.") + " The move depends on the surprise, not the headline."
            if card["past"] and candles:
                r = reaction(candles.get(sym) or [], e["ts"], p.get("dec", 5), t_offset)
                if r:
                    card["reaction"] = {sym: r}
            cards.append(card)
        cards.sort(key=lambda c: (c["past"], c["ts"] if not c["past"] else -c["ts"]))
        v = verdict(sym, info, events, headlines, now)
        blocks.append(dict(symbol=sym, price=p.get("price"), dec=p.get("dec", 5), chg=p.get("chg"), verdict=v, events=cards[:5],
                           headlines=relevant_headlines(sym, headlines)))
    return dict(symbols=blocks, cautions=CAUTIONS, auto_trade=False)


KEYWORDS = {"EURUSD": ("eur", "euro", "ecb", "fed", "dollar", "usd", "powell", "lagarde", "payroll", "cpi", "treasury"),
            "XAUUSD": ("gold", "xau", "bullion", "fed", "dollar", "yield", "treasury", "powell", "inflation", "payroll")}


def relevant_headlines(symbol: str, headlines: list[dict] | None, n: int = 5) -> list[dict]:
    kw = KEYWORDS.get(symbol.upper(), (symbol.lower(),))
    out = [h for h in headlines or [] if any(k in str(h.get("title", "")).lower() for k in kw)]
    return out[:n]
