"""More chart patterns, plus support / resistance zones.

Everything works on closed candles only and is rule based (no guessing). Tops are detected on the normal view and bottoms on
a mirrored view (prices times -1), so every bearish pattern has an exact bullish twin:

    Triple Top / Bottom, Rising / Falling Wedge, Bull / Bear Flag, Rectangle (range) with breakout,
    Ascending / Descending Channel, and more candle patterns (stars, harami, piercing / dark cloud,
    three soldiers / crows, tweezers, doji, inside bar).

Each pattern is a dict shaped like the ones in analysis.py: name, side (+1 buy / -1 sell / 0 neutral), status
("forming" or "confirmed"), time, brk_recent, optional neckline / target / sl, note, and `lines`
(trend lines to draw: [[iso_time, price], [iso_time, price]]). A confirmed pattern with a target and stop can become
a "Pattern: ..." trade candidate (analysis.py applies its reward-to-risk filter and grading).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, pivot_idx


def _iso(df, i) -> str:
    return df.index[int(i)].isoformat()


def _views(df):
    H, L, C = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    return {1: (H, L, C), -1: (-L, -H, -C)}


def _line(df, i1, p1, i2, p2, s=1):
    return [[_iso(df, i1), float(s * p1)], [_iso(df, i2), float(s * p2)]]


# ---------------------------------------------------------------- bearish-on-top patterns (mirrored for bottoms)
def _triple_top(H, L, C, pivH, a):
    if len(pivH) < 3:
        return None
    i1, i2, i3 = pivH[-3:]
    ps, last = [H[i1], H[i2], H[i3]], len(C) - 1
    if i2 - i1 < 4 or i3 - i2 < 4 or i3 < last - 90 or max(ps) - min(ps) > 0.4 * a:
        return None
    neck, top = L[i1: i3 + 1].min(), max(ps)
    if top - neck < 1.5 * a:
        return None
    brk = next((j for j in range(i3 + 1, last + 1) if C[j] < neck), None)
    if brk is not None:
        if last - brk > 10:
            return None
        status = "confirmed"
    else:
        if last - i3 > 40 or C[last] > top + 0.5 * a:
            return None
        status = "forming"
    return dict(status=status, idx=int(i3), neck=neck, top=top, target=neck - (top - neck), sl=top + 0.2 * a,
                brk=brk, lines=[(i1, neck, last, neck)])


def _wedge(H, L, C, pivH, pivL, a):
    """Rising wedge (both lines rising, the lower one faster, converging). Bearish. Mirror = falling wedge."""
    if len(pivH) < 3 or len(pivL) < 3:
        return None
    hs, ls, last = pivH[-3:], pivL[-3:], len(C) - 1
    start, end = min(hs[0], ls[0]), max(hs[-1], ls[-1])
    if end - start < 15 or end - start > 150 or last - end > 25:
        return None
    kh, bh = np.polyfit(hs, H[hs], 1)
    kl, bl = np.polyfit(ls, L[ls], 1)
    up = lambda x: kh * x + bh
    lo = lambda x: kl * x + bl
    if not (kh > 0 and kl > 0 and kl > 1.2 * kh and kl * (end - start) > 1.0 * a):
        return None
    w0, w1 = up(start) - lo(start), up(end) - lo(end)
    if w1 < 0.25 * a or w1 > 0.85 * w0:
        return None
    if max(abs(H[hs] - up(np.array(hs)))) > 0.5 * a or max(abs(L[ls] - lo(np.array(ls)))) > 0.5 * a:
        return None
    brk = next((j for j in range(end + 1, last + 1) if C[j] < lo(j) - 0.1 * a), None)
    if brk is not None:
        if last - brk > 5:
            return None
        status, ref = "confirmed", C[brk]
        neck = lo(brk)
    else:
        if C[last] > up(last) + 0.5 * a:
            return None
        status, ref = "forming", lo(last)
        neck = lo(last)
    return dict(status=status, idx=int(end), neck=neck, top=up(end), target=ref - w0, sl=H[hs[-1]] + 0.2 * a,
                brk=brk, lines=[(start, up(start), last, up(last)), (start, lo(start), last, lo(last))])


def _flag(H, L, C, a):
    """Bull flag: sharp rise (pole) then a tight, flat or slightly falling pause, then a close above it. Mirror = bear flag."""
    last = len(C) - 1
    if last < 30:
        return None
    lo_i = max(0, last - 45)
    ih = lo_i + int(np.argmax(H[lo_i: last - 2]))
    if last - ih < 4 or last - ih > 24:
        return None
    s0 = max(0, ih - 15)
    ps = s0 + int(np.argmin(L[s0: ih + 1]))
    pole = H[ih] - L[ps]
    if ih - ps < 3 or pole < 3.5 * a:
        return None
    for j in range(ih + 4, last + 1):
        x = np.arange(ih + 1, j)
        if len(x) < 3:
            continue
        k, b = np.polyfit(x, C[ih + 1: j], 1)
        hi = (H[ih + 1: j] - k * x).max()
        lo = (L[ih + 1: j] - k * x).min()
        if k > 0.03 * a or H[ih] - L[ih + 1: j].min() > 0.6 * pole or hi - lo > 0.6 * pole:
            continue
        upper = k * j + hi
        if C[j] > upper + 0.05 * a:
            if last - j > 3:
                return None
            return dict(status="confirmed", idx=int(ih), neck=upper, top=H[ih], target=C[j] + pole,
                        sl=L[ih + 1: j].min() - 0.2 * a, brk=j,
                        lines=[(ih + 1, k * (ih + 1) + hi, last, k * last + hi), (ih + 1, k * (ih + 1) + lo, last, k * last + lo)])
        if j == last:
            if last - ih < 5 or C[last] < k * last + lo - 0.1 * a:
                return None
            return dict(status="forming", idx=int(ih), neck=upper, top=H[ih], target=upper + pole,
                        sl=L[ih + 1: j].min() - 0.2 * a, brk=None,
                        lines=[(ih + 1, k * (ih + 1) + hi, last, k * last + hi), (ih + 1, k * (ih + 1) + lo, last, k * last + lo)])
    return None


# ---------------------------------------------------------------- both-direction patterns
def _rectangle(H, L, C, pivH, pivL, a):
    if len(pivH) < 2 or len(pivL) < 2:
        return None
    hs, ls, last = pivH[-3:], pivL[-3:], len(C) - 1
    hv, lv = H[hs], L[ls]
    if max(hv) - min(hv) > 0.4 * a or max(lv) - min(lv) > 0.4 * a:
        return None
    top, bot = float(hv.mean()), float(lv.mean())
    height = top - bot
    start, end = min(hs[0], ls[0]), max(hs[-1], ls[-1])
    if height < 1.5 * a or height > 8 * a or end - start < 12 or last - end > 25:
        return None
    brk, side = None, 0
    for j in range(end + 1, last + 1):
        if C[j] > top + 0.1 * a:
            brk, side = j, 1
            break
        if C[j] < bot - 0.1 * a:
            brk, side = j, -1
            break
    lines = [(start, top, last, top), (start, bot, last, bot)]
    if brk is None:
        return dict(name="Rectangle (range)", side=0, status="forming", idx=int(end), neck=None, lines=lines,
                    note=f"sideways between {bot:.5f} and {top:.5f}; trade the break of either side")
    if last - brk > 5:
        return None
    mid = (top + bot) / 2
    return dict(name="Rectangle Breakout " + ("Up" if side == 1 else "Down"), side=side, status="confirmed", idx=int(brk),
                neck=top if side == 1 else bot, target=(top + height) if side == 1 else (bot - height), sl=mid, brk=brk,
                lines=lines, note="closed outside the range; target is the range height")


def _channel(H, L, C, pivH, pivL, a):
    if len(pivH) < 3 or len(pivL) < 3:
        return None
    hs, ls, last = pivH[-3:], pivL[-3:], len(C) - 1
    start, end = min(hs[0], ls[0]), max(hs[-1], ls[-1])
    if end - start < 15 or end - start > 150 or last - end > 20:
        return None
    kh, bh = np.polyfit(hs, H[hs], 1)
    kl, bl = np.polyfit(ls, L[ls], 1)
    if kh * kl <= 0 or abs(kh - kl) > 0.25 * max(abs(kh), abs(kl)) or abs(kh) * (end - start) < 1.5 * a:
        return None
    if max(abs(H[hs] - (kh * np.array(hs) + bh))) > 0.5 * a or max(abs(L[ls] - (kl * np.array(ls) + bl))) > 0.5 * a:
        return None
    if not (kl * last + bl - 0.3 * a <= C[last] <= kh * last + bh + 0.3 * a):
        return None
    s = 1 if kh > 0 else -1
    return dict(name=("Ascending" if s == 1 else "Descending") + " Channel", side=s, status="forming", idx=int(end), neck=None,
                lines=[(start, kh * start + bh, last, kh * last + bh), (start, kl * start + bl, last, kl * last + bl)],
                note=("rising" if s == 1 else "falling") + " channel: with the trend, buy dips to the lower line" if s == 1
                else "falling channel: with the trend, sell rallies to the upper line")


# ---------------------------------------------------------------- candles
def more_candles(df: pd.DataFrame, a: float) -> list[tuple[str, int, str]]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    i = len(df) - 1
    if i < 3:
        return []
    body = lambda k: abs(c[k] - o[k])
    bull = lambda k: c[k] > o[k]
    bear = lambda k: c[k] < o[k]
    out: list[tuple[str, int, str]] = []
    if bear(i - 2) and body(i - 2) >= 0.6 * a and body(i - 1) <= 0.3 * body(i - 2) and bull(i) and c[i] > (o[i - 2] + c[i - 2]) / 2:
        out.append(("Morning Star", 1, "big red, small pause candle, then a green candle back above the middle"))
    if bull(i - 2) and body(i - 2) >= 0.6 * a and body(i - 1) <= 0.3 * body(i - 2) and bear(i) and c[i] < (o[i - 2] + c[i - 2]) / 2:
        out.append(("Evening Star", -1, "big green, small pause candle, then a red candle back below the middle"))
    if bear(i - 1) and body(i - 1) >= 0.8 * a and bull(i) and o[i] > c[i - 1] and c[i] < o[i - 1] and body(i) < body(i - 1):
        out.append(("Bullish Harami", 1, "small green candle inside the previous big red one"))
    if bull(i - 1) and body(i - 1) >= 0.8 * a and bear(i) and o[i] < c[i - 1] and c[i] > o[i - 1] and body(i) < body(i - 1):
        out.append(("Bearish Harami", -1, "small red candle inside the previous big green one"))
    if bear(i - 1) and body(i - 1) >= 0.8 * a and bull(i) and o[i] < c[i - 1] and (o[i - 1] + c[i - 1]) / 2 < c[i] < o[i - 1]:
        out.append(("Piercing Line", 1, "green candle closed above the middle of the previous red one"))
    if bull(i - 1) and body(i - 1) >= 0.8 * a and bear(i) and o[i] > c[i - 1] and o[i - 1] < c[i] < (o[i - 1] + c[i - 1]) / 2:
        out.append(("Dark Cloud Cover", -1, "red candle closed below the middle of the previous green one"))
    if all(bull(i - k) and body(i - k) >= 0.5 * a for k in (0, 1, 2)) and c[i] > c[i - 1] > c[i - 2] and o[i] > o[i - 1] > o[i - 2]:
        out.append(("Three White Soldiers", 1, "three strong green candles in a row"))
    if all(bear(i - k) and body(i - k) >= 0.5 * a for k in (0, 1, 2)) and c[i] < c[i - 1] < c[i - 2] and o[i] < o[i - 1] < o[i - 2]:
        out.append(("Three Black Crows", -1, "three strong red candles in a row"))
    if bull(i - 1) and bear(i) and abs(h[i] - h[i - 1]) <= 0.1 * a and h[i] - l[i] >= 0.5 * a:
        out.append(("Tweezer Top", -1, "two candles with the same high, the second one red"))
    if bear(i - 1) and bull(i) and abs(l[i] - l[i - 1]) <= 0.1 * a and h[i] - l[i] >= 0.5 * a:
        out.append(("Tweezer Bottom", 1, "two candles with the same low, the second one green"))
    rng = h[i] - l[i]
    if rng >= 0.8 * a and body(i) <= 0.1 * rng:
        out.append(("Doji", 0, "open and close almost equal: indecision"))
    if h[i] < h[i - 1] and l[i] > l[i - 1] and (h[i - 1] - l[i - 1]) >= 0.8 * a:
        out.append(("Inside Bar", 0, "the candle is inside the previous one: a break of either side is the signal"))
    return out


# ---------------------------------------------------------------- support / resistance zones
def sr_levels(df: pd.DataFrame, n: int = 3, lookback: int = 400, per_side: int = 3) -> list[dict]:
    """Price areas that turned the market at least twice (swing highs and lows grouped within half an ATR)."""
    d = df.iloc[-lookback:]
    if len(d) < 40:
        return []
    a = float(atr(df).iloc[-1])
    price = float(df["close"].iloc[-1])
    H, L = d["high"].to_numpy(float), d["low"].to_numpy(float)
    pts = [(H[i], i) for i in pivot_idx(H, n, "max")] + [(L[i], i) for i in pivot_idx(L, n, "min")]
    pts.sort()
    clusters: list[list[tuple[float, int]]] = []
    for p, i in pts:
        if clusters and p - np.mean([q for q, _ in clusters[-1]]) <= 0.5 * a:
            clusters[-1].append((p, i))
        else:
            clusters.append([(p, i)])
    out = []
    for cl in clusters:
        idxs = sorted(i for _, i in cl)
        touches = 1 + sum(1 for u, v in zip(idxs, idxs[1:]) if v - u >= 6)  # a flat stretch counts once
        if touches < 2:
            continue
        p = float(np.mean([q for q, _ in cl]))
        below = p < price
        out.append(dict(name=f"{'Support' if below else 'Resistance'} x{touches}", price=p, kind="sr",
                        t0=d.index[idxs[0]].isoformat(), touches=touches))
    below = sorted([x for x in out if x["price"] < price], key=lambda x: price - x["price"])[:per_side]
    above = sorted([x for x in out if x["price"] >= price], key=lambda x: x["price"] - price)[:per_side]
    return below + above


# ---------------------------------------------------------------- entry point
def more_patterns(df: pd.DataFrame, n: int = 3) -> list[dict]:
    a = float(atr(df).iloc[-1])
    if not a > 0 or len(df) < 60:
        return []
    last = len(df) - 1
    pats: list[dict] = []

    def add(name, side, r, note, s=1):
        pats.append(dict(
            name=name, side=side, status=r["status"], time=_iso(df, r["idx"]),
            brk_recent=r.get("brk") is not None and last - r["brk"] <= 3,
            neckline=float(s * r["neck"]) if r.get("neck") is not None else None,
            target=float(s * r["target"]), sl=float(s * r["sl"]), note=note,
            lines=[_line(df, i1, p1, i2, p2, s) for i1, p1, i2, p2 in r["lines"]]))

    for s, (H, L, C) in _views(df).items():
        pivH, pivL = pivot_idx(H, n, "max"), pivot_idx(L, n, "min")
        side = -s
        r = _triple_top(H, L, C, pivH, a)
        if r:
            add("Triple Top" if s == 1 else "Triple Bottom", side, r,
                f"three equal {'highs' if s == 1 else 'lows'}; neckline {s * r['neck']:.5f}; "
                f"{'broke' if r['status'] == 'confirmed' else 'watch for a break of'} the neckline", s)
        r = _wedge(H, L, C, pivH, pivL, a)
        if r:
            add("Rising Wedge" if s == 1 else "Falling Wedge", side, r,
                ("squeezing higher " if s == 1 else "squeezing lower ") + "with a shrinking range; "
                + ("broke the lower line" if r["status"] == "confirmed" else "a break of the "
                   + ("lower" if s == 1 else "upper") + " line is the signal"), s)
        r = _flag(H, L, C, a)
        if r:
            add("Bull Flag" if s == 1 else "Bear Flag", s, r,
                "sharp move then a tight pause; " + ("broke out in the direction of the move" if r["status"] == "confirmed"
                                                     else "a close beyond the pause continues the move"), s)

    ph, pl = pivot_idx(df["high"].to_numpy(float), n, "max"), pivot_idx(df["low"].to_numpy(float), n, "min")
    H, L, C = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    r = _rectangle(H, L, C, ph, pl, a)
    if r:
        pats.append(dict(name=r["name"], side=r["side"], status=r["status"], time=_iso(df, r["idx"]),
                         brk_recent=r.get("brk") is not None and last - r["brk"] <= 3,
                         neckline=float(r["neck"]) if r.get("neck") is not None else None,
                         target=float(r["target"]) if "target" in r else None, sl=float(r["sl"]) if "sl" in r else None,
                         note=r["note"], lines=[_line(df, *ln) for ln in r["lines"]]))
        for k in ("target", "sl"):
            if pats[-1][k] is None:
                del pats[-1][k]
    r = _channel(H, L, C, ph, pl, a)
    if r:
        pats.append(dict(name=r["name"], side=r["side"], status="forming", time=_iso(df, r["idx"]), brk_recent=False,
                         note=r["note"], lines=[_line(df, *ln) for ln in r["lines"]]))

    if any(p["name"].startswith("Rectangle") for p in pats):  # inside a range, "forming" triples only add noise
        pats = [p for p in pats if not (p["name"].startswith("Triple") and p["status"] == "forming")]

    for name, side, note in more_candles(df, a):
        pats.append(dict(name=name, side=side, status="confirmed", time=_iso(df, last), brk_recent=True, note=note))
    return pats
