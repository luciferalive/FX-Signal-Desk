"""Analysis engine: trend, key levels (PDH/PDL, Asia, session H/L, weekly, swings, round-number
handles), chart patterns, and a graded trade signal with SL/TP and plain-English reasons.

Everything returned is plain dict/list/str/float so the dashboard (JSON) and Telegram text
both use the same result. Uses only closed candles - no lookahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import pip_size
from .indicators import atr, ema, pivot_idx
from .modes import MODES
from .sessions import IST, NY, day_levels, session_labels, silver_bullet_now, window_mask
from .strategies import STRATEGIES
from .zones import dealing_range, find_fvgs, find_obs, open_levels, sma_values

HIGH_PROB_SESSIONS = ("London KZ", "NY AM KZ")


# ---------------------------------------------------------------- pivots / trend
def trend_state(df: pd.DataFrame, n: int = 3) -> dict:
    c = df["close"]
    e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200)
    score = (1 if c.iloc[-1] > e50.iloc[-1] else -1) + (1 if e21.iloc[-1] > e50.iloc[-1] else -1)
    if len(df) > 210:
        score += 1 if e50.iloc[-1] > e200.iloc[-1] else -1
    hi = pivot_idx(df["high"].to_numpy(), n, "max")
    lo = pivot_idx(df["low"].to_numpy(), n, "min")
    structure = "not enough swings"
    if len(hi) >= 2 and len(lo) >= 2:
        hh = df["high"].iloc[hi[-1]] > df["high"].iloc[hi[-2]]
        hl = df["low"].iloc[lo[-1]] > df["low"].iloc[lo[-2]]
        if hh and hl:
            structure, score = "higher highs + higher lows", score + 2
        elif not hh and not hl:
            structure, score = "lower highs + lower lows", score - 2
        else:
            structure = "mixed / ranging swings"
    label = "Bullish" if score >= 3 else "Bearish" if score <= -3 else "Ranging"
    return dict(label=label, score=int(score), structure=structure)


# ---------------------------------------------------------------- key levels
def _block(mask: np.ndarray):
    """Indices of the most recent contiguous True block."""
    w = np.where(mask)[0]
    if not len(w):
        return None
    end = w[-1]
    start = end
    while start - 1 >= 0 and mask[start - 1]:
        start -= 1
    return start, end


def _blocks(mask: np.ndarray):
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def key_levels(df: pd.DataFrame, symbol: str, n: int = 3, intraday: bool = True) -> list[dict]:
    """Every level carries t0 (when it formed) so the chart can start the line there."""
    price = float(df["close"].iloc[-1])
    pip = pip_size(symbol)
    idx = df.index
    ny = idx.tz_convert(NY)
    out: list[dict] = []

    def add(name, p, kind, t0=None):
        if p is None:
            return
        p = float(p)
        if np.isnan(p):
            return
        out.append(dict(name=name, price=p, kind=kind, t0=t0.isoformat() if t0 is not None else None))

    shifted = (ny + pd.Timedelta(hours=7)).normalize()
    day_i = int(np.asarray(shifted == shifted[-1]).argmax())
    lv = day_levels(df)
    add("PDH", lv["pd_h"].iloc[-1], "liquidity", idx[day_i])
    add("PDL", lv["pd_l"].iloc[-1], "liquidity", idx[day_i])

    if intraday:
        add("Today High", df["high"].iloc[day_i:].max(), "day", idx[day_i])
        add("Today Low", df["low"].iloc[day_i:].min(), "day", idx[day_i])
        blocks = _blocks(window_mask(idx, "Asia"))
        if blocks and blocks[-1][1] == len(df) - 1:
            blocks = blocks[:-1]  # still forming
        if blocks:
            s0, e0 = blocks[-1]
            seg = df.iloc[s0: e0 + 1]
            add("Asia High", seg["high"].max(), "liquidity", idx[s0])
            add("Asia Low", seg["low"].min(), "liquidity", idx[s0])
        for name in ("London KZ", "NY AM KZ"):
            bl = _blocks(window_mask(idx, name))
            if bl:
                s0, e0 = bl[-1]
                seg = df.iloc[s0: e0 + 1]
                add(f"{name} High", seg["high"].max(), "session", idx[s0])
                add(f"{name} Low", seg["low"].min(), "session", idx[s0])

    # previous week high / low (forex week rolls Sun 17:00 NY)
    wk = pd.Series(shifted.tz_localize(None).to_period("W-SUN"), index=idx)
    weeks = wk.unique()
    if len(weeks) >= 2:
        pw = df[wk == weeks[-2]]
        w0 = int(np.asarray(wk == weeks[-1]).argmax())
        add("PWH", pw["high"].max(), "liquidity", idx[w0])
        add("PWL", pw["low"].min(), "liquidity", idx[w0])

    for z in open_levels(df):
        if intraday or z["name"] == "Weekly Open":
            out.append(z)

    for j, i in enumerate(reversed(pivot_idx(df["high"].to_numpy(), n, "max")[-2:])):
        add(f"Swing High {j + 1}", df["high"].iloc[i], "swing", idx[i])
    for j, i in enumerate(reversed(pivot_idx(df["low"].to_numpy(), n, "min")[-2:])):
        add(f"Swing Low {j + 1}", df["low"].iloc[i], "swing", idx[i])

    # round-number handles: every 50 pips; x00 = "handle" (big figure), x50 = half-handle
    step = 50 * pip
    base = np.floor(price / step) * step
    for p in (base - step, base, base + step, base + 2 * step):
        if abs(p - price) / pip <= 150:
            big = abs((p / (100 * pip)) - round(p / (100 * pip))) < 1e-6
            add("Handle" if big else "Half-handle", p, "handle")

    # merge levels that sit on the same price (keep the most meaningful name)
    prio = {"liquidity": 0, "session": 1, "open": 2, "swing": 3, "day": 4, "handle": 5}
    uniq: list[dict] = []
    for L in sorted(out, key=lambda x: prio.get(x["kind"], 9)):
        if not any(abs(L["price"] - u["price"]) < 0.5 * pip for u in uniq):
            uniq.append(L)
    out = uniq
    for L in out:
        L["dist_pips"] = round((L["price"] - price) / pip, 1)
    out.sort(key=lambda x: abs(x["price"] - price))
    return out


# ---------------------------------------------------------------- chart patterns
def _views(df):
    """+1 view = tops (as is). -1 view = mirrored so bottoms look like tops."""
    H, L, C = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    return {1: (H, L, C), -1: (-L, -H, -C)}


def _double_top(H, L, C, pivH, a):
    if len(pivH) < 2:
        return None
    i1, i2 = pivH[-2], pivH[-1]
    p1, p2, last = H[i1], H[i2], len(C) - 1
    if i2 - i1 < 5 or abs(p1 - p2) > 0.35 * a:
        return None
    neck, top = L[i1: i2 + 1].min(), max(p1, p2)
    if top - neck < 1.5 * a:
        return None
    brk = next((j for j in range(i2 + 1, last + 1) if C[j] < neck), None)
    if brk is not None:
        if last - brk > 10:
            return None
        status = "confirmed"
    else:
        if last - i2 > 40 or C[last] > top + 0.5 * a:
            return None
        status = "forming"
    return dict(status=status, idx=int(i2), neck=neck, top=top, target=neck - (top - neck),
                sl=top + 0.2 * a, brk=brk)


def _head_shoulders(H, L, C, pivH, a):
    if len(pivH) < 3:
        return None
    i1, i2, i3 = pivH[-3:]
    p1, p2, p3, last = H[i1], H[i2], H[i3], len(C) - 1
    if not (p2 > p1 + 0.5 * a and p2 > p3 + 0.5 * a):
        return None
    if abs(p1 - p3) > 0.4 * (p2 - min(p1, p3)):
        return None
    neck = (L[i1: i2 + 1].min() + L[i2: i3 + 1].min()) / 2
    brk = next((j for j in range(i3 + 1, last + 1) if C[j] < neck), None)
    if brk is not None:
        if last - brk > 10:
            return None
        status = "confirmed"
    else:
        if last - i3 > 30 or C[last] > p3 + 0.3 * a:
            return None
        status = "forming"
    return dict(status=status, idx=int(i3), neck=neck, top=p2, target=neck - (p2 - neck),
                sl=max(p3, p1) + 0.2 * a, brk=brk)


def _flat_top_triangle(H, L, C, pivH, pivL, a):
    """Flat highs + rising lows (ascending triangle; mirrored view = descending)."""
    if len(pivH) < 3 or len(pivL) < 3:
        return None
    hs, ls = [H[i] for i in pivH[-3:]], [L[i] for i in pivL[-3:]]
    last = len(C) - 1
    if pivH[-3] < last - 120 or max(hs) - min(hs) > 0.5 * a:
        return None
    if not (ls[1] - ls[0] >= 0.2 * a and ls[2] - ls[1] >= 0.2 * a):
        return None
    top = max(hs)
    brk = next((j for j in range(max(pivH[-1], pivL[-1]) + 1, last + 1) if C[j] > top + 0.1 * a), None)
    if brk is not None and last - brk > 5:
        return None
    if brk is None and C[last] < ls[2] - 0.3 * a:
        return None
    return dict(status="confirmed" if brk is not None else "forming", idx=int(pivL[-1]), top=top,
                target=top + (top - ls[0]), sl=ls[2] - 0.2 * a, brk=brk)


def candle_patterns(df, a):
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    i = len(df) - 1
    out = []
    body, rng = abs(c[i] - o[i]), h[i] - l[i]
    if c[i - 1] < o[i - 1] and c[i] > o[i] and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body >= 0.8 * a:
        out.append(("Bullish Engulfing", 1))
    if c[i - 1] > o[i - 1] and c[i] < o[i] and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body >= 0.8 * a:
        out.append(("Bearish Engulfing", -1))
    if rng >= 0.8 * a and rng > 0:
        lower, upper = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
        if lower / rng >= 0.6 and body / rng <= 0.3:
            out.append(("Hammer / bullish pin bar", 1))
        if upper / rng >= 0.6 and body / rng <= 0.3:
            out.append(("Shooting star / bearish pin bar", -1))
    return out


def detect_patterns(df: pd.DataFrame, levels: list[dict], n: int = 3) -> list[dict]:
    a = float(atr(df).iloc[-1])
    last = len(df) - 1
    t = lambda i: df.index[i].isoformat()
    pats: list[dict] = []

    views = _views(df)
    for s, (H, L, C) in views.items():
        pivH, pivL = pivot_idx(H, n, "max"), pivot_idx(L, n, "min")
        for fn, nm_top, nm_bot in ((_double_top, "Double Top", "Double Bottom"),
                                   (_head_shoulders, "Head & Shoulders", "Inverse Head & Shoulders")):
            r = fn(H, L, C, pivH, a)
            if r:
                side = -1 if s == 1 else 1
                pats.append(dict(name=nm_top if s == 1 else nm_bot, side=side, status=r["status"],
                                 time=t(r["idx"]), brk_recent=r["brk"] is not None and last - r["brk"] <= 3,
                                 neckline=s * r["neck"], target=s * r["target"], sl=s * r["sl"],
                                 note=f"neckline {s * r['neck']:.5f}; "
                                      f"{'broke' if r['status'] == 'confirmed' else 'watch for break of'} neckline"))
        r = _flat_top_triangle(H, L, C, pivH, pivL, a)
        if r:
            side = 1 if s == 1 else -1
            pats.append(dict(name="Ascending Triangle" if s == 1 else "Descending Triangle", side=side,
                             status=r["status"], time=t(r["idx"]),
                             brk_recent=r["brk"] is not None and last - r["brk"] <= 3,
                             neckline=s * r["top"], target=s * r["target"], sl=s * r["sl"],
                             note=f"flat {'resistance' if s == 1 else 'support'} {s * r['top']:.5f}"))

    # symmetrical triangle: lower highs + higher lows
    hi, lo = pivot_idx(df["high"].to_numpy(), n, "max")[-3:], pivot_idx(df["low"].to_numpy(), n, "min")[-3:]
    if len(hi) == 3 and len(lo) == 3 and hi[0] > last - 120:
        hv, lv_ = df["high"].to_numpy()[hi], df["low"].to_numpy()[lo]
        if hv[0] - hv[1] >= 0.2 * a and hv[1] - hv[2] >= 0.2 * a and lv_[1] - lv_[0] >= 0.2 * a and lv_[2] - lv_[1] >= 0.2 * a:
            if lv_[2] < df["close"].iloc[-1] < hv[2]:
                pats.append(dict(name="Symmetrical Triangle", side=0, status="forming", time=t(hi[-1]),
                                 brk_recent=False, note="compression - wait for a close beyond either side"))

    for name, side in candle_patterns(df, a):
        pats.append(dict(name=name, side=side, status="confirmed", time=t(last), brk_recent=True, note="last closed candle"))

    # liquidity sweeps on the last 3 bars
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    for L_ in levels:
        if L_["kind"] != "liquidity":
            continue
        for j in range(max(last - 2, 0), last + 1):
            if h[j] > L_["price"] > c[j] and L_["price"] > c[last - 3]:
                pats.append(dict(name=f"Liquidity sweep of {L_['name']}", side=-1, status="confirmed", time=t(j),
                                 brk_recent=True, note="wick above then closed back below (stop-run)"))
                break
            if l[j] < L_["price"] < c[j] and L_["price"] < c[last - 3]:
                pats.append(dict(name=f"Liquidity sweep of {L_['name']}", side=1, status="confirmed", time=t(j),
                                 brk_recent=True, note="wick below then closed back above (stop-run)"))
                break
    return pats


# ---------------------------------------------------------------- signal composer
def _fresh_strategy_signals(df, symbol, mode, lookback=3):
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    last = len(df) - 1
    out = []
    for cls in STRATEGIES.values():
        if mode not in cls.PRESETS:
            continue
        st = cls(mode)
        for s in st.generate(df, symbol):
            if s.idx < last - lookback + 1:
                continue
            seg_h, seg_l = h[s.idx + 1: last + 1], l[s.idx + 1: last + 1]
            if s.side == 1 and ((seg_l <= s.sl).any() or (s.entry_type == "limit" and (seg_h >= s.tp).any())):
                continue
            if s.side == -1 and ((seg_h >= s.sl).any() or (s.entry_type == "limit" and (seg_l <= s.tp).any())):
                continue
            filled = s.entry_type == "limit" and ((seg_l <= s.entry).any() if s.side == 1 else (seg_h >= s.entry).any())
            out.append((s, "active (limit already touched)" if filled else "pending limit" if s.entry_type == "limit" else "market"))
    return out


def _sma_agrees(smas: dict, price: float, side: int) -> bool:
    """Buys want price above the long SMA with the 50 above it (and the reverse for sells)."""
    s50, s100, s200 = smas.get(50), smas.get(100), smas.get(200)
    ref = s200 if s200 is not None else s100
    if s50 is None or ref is None:
        return False
    return (price > ref and s50 > ref) if side == 1 else (price < ref and s50 < ref)


def analyze(symbol: str, mode: str, df: pd.DataFrame, df_htf: pd.DataFrame | None = None, htf_name: str = "",
            strict: bool = True, min_ratio: float = 0.6, *, interval: str | None = None,
            signals: bool = True, intraday: bool | None = None) -> dict:
    """mode: the tuned view ('scalp' / 'swing' / 'daily') or 'view' for analysis-only timeframes.
    signals=False gives levels, zones, averages, patterns and trend but never a trade signal."""
    pip = pip_size(symbol)
    dec = 3 if pip == 0.01 else 2 if pip == 0.1 else 5
    interval = interval or MODES[mode]["interval"]
    if intraday is None:
        intraday = mode != "daily"
    a = float(atr(df).iloc[-1])
    price = float(df["close"].iloc[-1])
    sess = str(session_labels(df.index)[-1]) if intraday else "Daily candle"
    sb = silver_bullet_now(df.index) if mode == "scalp" and intraday else None
    trend = trend_state(df)
    htf = trend_state(df_htf) if df_htf is not None and len(df_htf) > 60 else None
    levels = key_levels(df, symbol, intraday=intraday)
    if interval in ("1w", "1M"):  # on these charts one candle is a week / month, so rename the "previous day" levels
        unit = "Week" if interval == "1w" else "Month"
        ren = {"PDH": f"Prev {unit} High", "PDL": f"Prev {unit} Low"}
        if interval == "1M":
            ren["Weekly Open"] = "Monthly Open"
        levels = [dict(L, name=ren.get(L["name"], L["name"])) for L in levels if L["name"] not in ("PWH", "PWL")]
    pats = detect_patterns(df, levels)
    fvgs, obs = find_fvgs(df), find_obs(df)
    rng = dealing_range(df)
    smas = sma_values(df)

    cands = []
    for s, status in (_fresh_strategy_signals(df, symbol, mode) if signals else []):
        cands.append(dict(side=s.side, entry=s.entry, sl=s.sl, tp=s.tp, rr=s.rr, entry_type=s.entry_type,
                          status=status, strategy=f"{s.strategy.upper()} {mode}", why=s.reason, expiry=s.expiry,
                          time=s.time.isoformat()))
    for p in (pats if signals else []):
        if p["side"] and p["status"] == "confirmed" and p.get("brk_recent") and "target" in p:
            risk = abs(price - p["sl"])
            reward = abs(p["target"] - price)
            if risk > 0 and reward / risk >= 1.5 and (p["sl"] - price) * p["side"] < 0:
                cands.append(dict(side=p["side"], entry=price, sl=p["sl"], tp=p["target"], rr=round(reward / risk, 2),
                                  entry_type="market", status="market (pattern breakout)",
                                  strategy=f"Pattern: {p['name']}", why=f"{p['name']} broke its neckline", expiry=0,
                                  time=p["time"]))

    signal = None
    best = (-1, -1.0)
    rejected = []
    for c in cands:
        side, entry = c["side"], c["entry"]
        want = "Bullish" if side == 1 else "Bearish"
        opp = "Bearish" if side == 1 else "Bullish"
        near = [L for L in levels if abs(L["price"] - entry) <= 0.6 * a and L["kind"] not in ("handle", "day")]
        hand = [L for L in levels if L["kind"] == "handle" and abs(L["price"] - entry) <= 0.4 * a]
        agree = [p for p in pats if p["side"] == side and p["name"] not in c["why"]
                 and not c["strategy"].endswith(p["name"])][:2]
        zone = next((z for z in sorted(fvgs + obs, key=lambda z: z["dist"])
                     if z["side"] == side and z["bottom"] - 0.3 * a <= entry <= z["top"] + 0.3 * a), None)
        pd_ok = bool(rng) and ((side == 1 and entry < rng["eq"]) or (side == -1 and entry > rng["eq"]))
        in_kz = sess in HIGH_PROB_SESSIONS or sess == "London Close" or bool(sb)

        checks = [("Trend aligned", trend["label"] == want)]
        if htf is not None:
            checks.append(("Higher timeframe aligned", htf["label"] == want))
        checks.append(("Moving averages agree", _sma_agrees(smas, price, side)))
        if mode == "scalp":
            checks.append(("In a killzone", in_kz))
        checks += [
            ("At a key level", bool(near)),
            ("FVG / order block at entry", zone is not None),
            ("Pattern / sweep support", bool(agree)),
            ("Right side of premium/discount", pd_ok),
            ("RR of 2 or better", c["rr"] >= 2),
        ]
        passed = [n for n, ok in checks if ok]
        missing = [n for n, ok in checks if not ok]
        need = int(np.ceil(min_ratio * len(checks)))
        against = trend["label"] == opp or bool(htf and htf["label"] == opp)
        if strict and (against or len(passed) < need):
            why = "trades against the trend" if against else \
                f"only {len(passed)}/{len(checks)} confirmations (need {need}; missing: {', '.join(missing)})"
            rejected.append(f"Rejected {'BUY' if side == 1 else 'SELL'} from {c['strategy']}: {why}")
            continue

        reasons = [c["why"]]
        if trend["label"] == want:
            reasons.append(f"With the {interval} trend ({trend['label'].lower()}: {trend['structure']})")
        elif trend["label"] == opp:
            reasons.append(f"AGAINST the {interval} trend ({trend['label'].lower()}) - counter-trend, smaller size")
        else:
            reasons.append(f"{interval} trend is ranging ({trend['structure']})")
        if htf:
            reasons.append(f"Higher timeframe {htf_name} is {htf['label'].lower()}"
                           + (" - agrees" if htf["label"] == want else " - disagrees" if htf["label"] == opp else ""))
        if smas.get(200) is not None or smas.get(100) is not None:
            bits = [f"SMA{k} {v:.{dec}f}" for k, v in smas.items() if v is not None]
            reasons.append(f"Price {price:.{dec}f} vs " + ", ".join(bits)
                           + (" - agree" if _sma_agrees(smas, price, side) else " - mixed"))
        if sb:
            reasons.append(f"Inside the {sb} window")
        elif sess in HIGH_PROB_SESSIONS:
            reasons.append(f"Inside {sess} (high-probability window)")
        if near:
            reasons.append("Entry near " + ", ".join(f"{L['name']} {L['price']:.{dec}f}" for L in near[:2]))
        if zone:
            reasons.append(f"Entry sits in a {'bullish' if zone['side'] == 1 else 'bearish'} {zone['kind']} "
                           f"{zone['bottom']:.{dec}f}-{zone['top']:.{dec}f} ({zone['status']})")
        if rng:
            reasons.append(f"Entry in {'discount' if entry < rng['eq'] else 'premium'} of the range "
                           f"{rng['lo']:.{dec}f}-{rng['hi']:.{dec}f} (EQ {rng['eq']:.{dec}f})")
        if hand:
            reasons.append(f"Round-number {hand[0]['name'].lower()} at {hand[0]['price']:.{dec}f} close by")
        for p in agree:
            reasons.append(f"{p['name']} supports it ({p['status']})")
        reasons.append(f"Risk {abs(entry - c['sl']) / pip:.1f} pips, reward {abs(c['tp'] - entry) / pip:.1f} pips (RR {c['rr']:.1f})")

        score = len(passed)
        if (score / len(checks), c["rr"]) > best:
            best = (score / len(checks), c["rr"])
            risk = abs(entry - c["sl"])
            ratio = score / len(checks)
            signal = dict(side="BUY" if side == 1 else "SELL", entry_type=c["entry_type"], status=c["status"],
                          entry=entry, sl=c["sl"], tp1=entry + side * risk, tp2=c["tp"], rr=c["rr"],
                          risk_pips=round(risk / pip, 1), strategy=c["strategy"], score=score, of=len(checks),
                          grade="A" if ratio >= 0.8 else "B" if ratio >= 0.7 else "C",
                          time=c["time"], expiry=c["expiry"], reasons=reasons,
                          checks=dict(passed=passed, missing=missing),
                          cancel=(f"Cancel if price closes beyond SL, or limit not filled within {c['expiry']} bars"
                                  if c["entry_type"] == "limit" else "Skip if price has already run >0.5R from entry"),
                          manage="Take partial at TP1 (1R) and move SL to breakeven; let the rest run to TP2.")

    watch = []
    if not signal:
        watch += rejected[:2]
        above = [L for L in levels if L["price"] > price and L["kind"] != "day"][:1]
        below = [L for L in levels if L["price"] < price and L["kind"] != "day"][:1]
        for L in above + below:
            watch.append(f"Nearest {L['name']} {L['price']:.{dec}f} ({L['dist_pips']:+.1f} pips): "
                         f"{'a sweep above then close back below = short setup' if L['price'] > price else 'a sweep below then close back above = long setup'}")
        near_z = sorted(fvgs + obs, key=lambda z: z["dist"])[:2]
        for z in near_z:
            watch.append(f"Unfilled {'bullish' if z['side'] == 1 else 'bearish'} {z['kind']} {z['bottom']:.{dec}f}-{z['top']:.{dec}f}: "
                         f"a reaction there in the {'buy' if z['side'] == 1 else 'sell'} direction is a possible entry")
        for p in pats:
            if p["status"] == "forming":
                watch.append(f"{p['name']} forming - {p['note']}")

    return dict(symbol=symbol, mode=mode, interval=interval, price=price, price_dec=dec, atr_pips=round(a / pip, 1),
                time=df.index[-1].isoformat(), session=sess, trend=trend,
                htf=dict(name=htf_name, **htf) if htf else None, smas={str(k): v for k, v in smas.items()},
                levels=levels, patterns=pats, zones=dict(fvgs=fvgs, obs=obs), range=rng, signal=signal, watch=watch)


# ---------------------------------------------------------------- text
def format_text(an: dict) -> str:
    d, s = an["price_dec"], an["signal"]
    t = pd.Timestamp(an["time"])
    stamp = f"{t.tz_convert(IST):%d %b %H:%M} IST / {t.tz_convert(NY):%H:%M} NY"
    head = f"{an['symbol']} {an['mode']} ({an['interval']}) | price {an['price']:.{d}f} | {an['session']} | {stamp}"
    if not s:
        lines = [f"⚪ NO TRADE - {head}", f"Trend: {an['trend']['label']} ({an['trend']['structure']})", "Wait for:"]
        lines += [f"• {w}" for w in an["watch"][:4]] or ["• nothing clean right now"]
        return "\n".join(lines)
    icon = "🟢" if s["side"] == "BUY" else "🔴"
    lines = [
        f"{icon} {s['side']} {an['symbol']} - {s['strategy']} | Grade {s['grade']} ({s['score']}/{s['of']})",
        head,
        f"Entry ({s['status'] if s['status'].startswith(s['entry_type']) else s['entry_type'] + ', ' + s['status']}): {s['entry']:.{d}f}",
        f"SL: {s['sl']:.{d}f}  ({s['risk_pips']} pips)",
        f"TP1 (1R): {s['tp1']:.{d}f}   TP2: {s['tp2']:.{d}f}  (RR {s['rr']:.1f})",
        f"Confirmations {s['score']}/{s['of']}: " + ", ".join(["✓ " + x for x in s["checks"]["passed"]] + ["✗ " + x for x in s["checks"]["missing"]]),
        "Why:",
        *[f"• {r}" for r in s["reasons"]],
    ]
    near = [L for L in an["levels"] if abs(L["dist_pips"]) <= 80][:5]
    if near:
        lines.append("Levels: " + " | ".join(f"{L['name']} {L['price']:.{d}f} ({L['dist_pips']:+.0f}p)" for L in near))
    zs = sorted(an["zones"]["fvgs"] + an["zones"]["obs"], key=lambda z: z["dist"])[:3]
    if zs:
        lines.append("Zones: " + " | ".join(f"{'bull' if z['side'] == 1 else 'bear'} {z['kind']} {z['bottom']:.{d}f}-{z['top']:.{d}f}" for z in zs))
    if an["patterns"]:
        lines.append("Patterns: " + ", ".join(f"{p['name']} [{p['status']}]" for p in an["patterns"][:4]))
    lines += [s["manage"], s["cancel"], "Not financial advice - use proper risk (1% max)."]
    return "\n".join(lines)
