"""Builds the dashboard: one page (docs/index.html) plus small data files (docs/data/*.json).

The page loads data/index.json first (pairs, which have signals), then one file per pair when you open it, so
adding pairs does not slow the page down. The chart is drawn on a canvas with no chart library, so FVGs, order
blocks, levels, SMAs and the trade box all sit on the candles. Light, dark and auto themes. Fundamental news
comes from fxbot.news. With inline=True everything is packed into a single html file (used for the demo)."""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

from . import DEFAULT_SYMBOLS
from .analysis import analyze
from .data import load_frames, resample, synthetic
from .modes import TIMEFRAMES
from .news import fetch_news, sample_news
from .scanner import closed_only
from .sessions import session_labels
from .zones import sma_series

IST_OFFSET = 19800  # the chart axis shows India time
N_CANDLES = {"1m": 700, "5m": 600}
DEFAULT_N = 500
TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
WRAP = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'


def _t(x) -> int:
    return int(pd.Timestamp(x).timestamp()) + IST_OFFSET


def _clean(o):
    """Plain JSON: numpy -> python, NaN -> None."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def _candles(df: pd.DataFrame, dec: int, n: int):
    d = df.iloc[-n:]
    sess = session_labels(d.index)
    return [[_t(t), round(float(o), dec), round(float(h), dec), round(float(l), dec), round(float(c), dec), str(s)]
            for t, o, h, l, c, s in zip(d.index, d["open"], d["high"], d["low"], d["close"], sess)]


def _stamp(an: dict) -> dict:
    """Convert every ISO time in the analysis to chart seconds (IST axis)."""
    for L in an["levels"]:
        L["t"] = _t(L["t0"]) if L.get("t0") else None
    for grp in an["zones"].values():
        for z in grp:
            z["t"] = _t(z["t0"])
    if an.get("range"):
        an["range"]["t"] = _t(an["range"]["t0"])
    for p in an["patterns"]:
        p["t"] = _t(p["time"])
    if an["signal"]:
        an["signal"]["t"] = _t(an["signal"]["time"])
    return an


# ---------------------------------------------------------------- data sources
def live_frames(symbols: list[str]) -> dict:
    """Real prices from Yahoo for every timeframe. The still-forming candle is dropped, except on weekly/monthly."""
    now = pd.Timestamp.now(tz="UTC")
    frames = load_frames(symbols, list(TIMEFRAMES))
    for fr in frames.values():
        for tf in list(fr):
            if tf not in ("1w", "1M"):
                fr[tf] = closed_only(fr[tf], tf, now)
    return frames


def _demo_base(sym: str) -> float:
    if sym.startswith("XAU"):
        return 2650.0
    if sym.endswith("JPY"):
        return 190.0 if sym.startswith(("GBP", "EUR")) else 150.0
    return {"GBPUSD": 1.30, "AUDUSD": 0.66, "NZDUSD": 0.60, "USDCAD": 1.36, "USDCHF": 0.88, "EURGBP": 0.84}.get(sym, 1.10)


def demo_frames(symbols: list[str]) -> dict:
    """Made-up prices for every timeframe, all derived from one series so they agree with each other."""
    out = {}
    for sym in symbols:
        seed = sum(map(ord, sym))
        base = _demo_base(sym)
        raw = synthetic("5m", days=1000, seed=seed, base=base, step_min=5)
        fr = {"5m": raw.iloc[-4000:]}
        fr["15m"] = resample(raw, "15m").iloc[-6000:]
        fr["30m"] = resample(raw, "30m").iloc[-6000:]
        h1 = resample(raw, "1h")
        fr["1h"] = h1.iloc[-6000:]
        fr["4h"] = resample(h1, "4h")
        fr["1d"] = resample(h1, "1d")
        # weekly and monthly need years of history for the 200-period average, so use a longer made-up series
        long = synthetic("15m", days=2600, seed=seed + 2, base=base, step_min=15)
        k = float(raw["close"].iloc[-1]) / float(long["close"].iloc[-1])
        long = long.assign(open=long["open"] * k, high=long["high"] * k, low=long["low"] * k, close=long["close"] * k)
        fr["1w"] = resample(long, "1w")
        fr["1M"] = resample(long, "1M")
        one = synthetic("1m", days=6, seed=seed + 1, base=float(raw["close"].iloc[-1]), step_min=1)
        shift = raw.index[-1] - one.index[-1]
        one.index = one.index + shift  # end at the same moment as the other timeframes
        k1 = float(raw["close"].iloc[-1]) / float(one["close"].iloc[-1])  # end at the same price too
        one = one.assign(open=one["open"] * k1, high=one["high"] * k1, low=one["low"] * k1, close=one["close"] * k1)
        fr["1m"] = one
        out[sym] = fr
    return out


# ---------------------------------------------------------------- analysis
def analyse_pair(sym: str, frames: dict) -> dict:
    """{timeframe: {an, candles, sma}} for one pair. A timeframe that fails is skipped, not fatal."""
    out = {}
    for tf, spec in TIMEFRAMES.items():
        df = frames.get(tf)
        if df is None or len(df) < 60:
            continue
        htf = frames.get(spec["htf"]) if spec["htf"] else None
        if spec["mode"] and spec["htf"] and htf is None:
            continue
        try:
            an = _stamp(analyze(sym, spec["mode"] or "view", df, htf, spec["htf_name"], interval=tf,
                                signals=spec["mode"] is not None, intraday=spec["intraday"]))
            dec, n = an["price_dec"], N_CANDLES.get(tf, DEFAULT_N)
            out[tf] = dict(an=an, candles=_candles(df, dec, n), sma=sma_series(df, n, dec=dec))
        except Exception as e:  # one broken timeframe must not kill the page
            print(f"[dashboard] {sym} {tf}: {type(e).__name__}: {e}")
    return out


def summarize(sym: str, tfs: dict) -> dict:
    sig = {tf: dict(side=d["an"]["signal"]["side"], grade=d["an"]["signal"]["grade"], strategy=d["an"]["signal"]["strategy"])
           for tf, d in tfs.items() if d["an"]["signal"]}
    first = next(d for tf, d in tfs.items())  # TIMEFRAMES is ordered smallest first, so this is the freshest price
    return dict(symbol=sym, dec=first["an"]["price_dec"], price=first["an"]["price"], sig=sig, have=list(tfs))


def build(symbols=None, out_dir: str = "docs", frames=None, news=None, demo: bool = False, inline: bool = False) -> str:
    """frames: {symbol: {tf: candles}} or None for live Yahoo data. Returns the path of index.html."""
    symbols = symbols or DEFAULT_SYMBOLS
    if frames is None:
        frames = live_frames(symbols)
    news = news if news is not None else fetch_news()
    pairs, index_pairs = {}, []
    for sym in symbols:
        tfs = analyse_pair(sym, frames.get(sym, {}))
        if tfs:
            pairs[sym] = dict(symbol=sym, tfs=tfs)
            index_pairs.append(summarize(sym, tfs))
    if not pairs:
        raise RuntimeError("no data for any symbol")
    tf_list = [dict(key=k, label=v["label"], intraday=v["intraday"], signals=v["mode"] is not None, tv=v["tv"])
               for k, v in TIMEFRAMES.items()]
    index = dict(generated=pd.Timestamp.now(tz="UTC").isoformat(), demo=demo, tfs=tf_list, pairs=index_pairs)

    tpl = open(TEMPLATE, encoding="utf-8").read()
    os.makedirs(out_dir, exist_ok=True)
    if inline:
        blob = json.dumps(_clean(dict(index=index, news=news, pairs=pairs)), separators=(",", ":"))
    else:
        blob = "null"
        os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
        dump = lambda name, obj: open(os.path.join(out_dir, "data", name), "w", encoding="utf-8").write(
            json.dumps(_clean(obj), separators=(",", ":")))
        dump("index.json", index)
        dump("news.json", news)
        for sym, d in pairs.items():
            dump(f"{sym}.json", d)
    head, _, body = tpl.partition("<style>")  # the template starts with <title> and the font link, then <style>
    page = WRAP + head + "<style>" + body
    page = page.replace("__INLINE__", blob.replace("</", "<\\/")).replace("</style>", "</style></head><body>", 1)
    page += "</body></html>"
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
