"""Strategies. Each one returns Signals using ONLY data up to the signal bar (no lookahead).

ICT (simplified, mechanical):
  1. Liquidity sweep: price wicks beyond Asia H/L, prior-day H/L or a confirmed swing
     point, then closes back inside (stop-run).
  2. Displacement + Market Structure Shift: a strong candle (body >= k*ATR) breaks
     structure in the opposite direction and leaves a Fair Value Gap (FVG).
  3. Entry: limit order at the FVG midpoint. SL beyond the sweep extreme. TP = RR * risk.
  Scalp mode only trades inside London / NY-AM killzones; swing mode runs on 4H with an
  EMA bias filter.

Set & Forget (my interpretation - swap in your exact rules, see README):
  Higher-timeframe trend (EMA50 > EMA200, rising), pullback to EMA21, trigger when price
  breaks the prior candle's high/low. SL beyond recent swing, fixed RR, then walk away.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr, ema, pivot_idx, swing_points
from .sessions import day_levels, killzone_mask, session_labels


@dataclass
class Signal:
    idx: int
    time: pd.Timestamp
    symbol: str
    strategy: str
    mode: str
    side: int  # +1 long, -1 short
    entry: float
    sl: float
    tp: float
    rr: float
    entry_type: str = "limit"  # "limit" | "market"
    expiry: int = 6  # bars a limit order stays alive
    reason: str = ""
    session: str = ""


class Strategy:
    name = ""
    PRESETS: dict = {}

    def __init__(self, mode: str = "swing", **overrides):
        if mode not in self.PRESETS:
            raise ValueError(f"{self.name}: mode must be one of {list(self.PRESETS)}")
        self.mode = mode
        self.preset = {**self.PRESETS[mode], **overrides}

    def generate(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        raise NotImplementedError


class ICT(Strategy):
    name = "ict"
    PRESETS = {
        "scalp": dict(interval="15m", rr=2.0, max_bars=8, disp_atr=0.9, kz=True, expiry=6,
                      sl_buf=0.15, swing_n=3, cooldown=8, bias_ema=None,
                      levels=("asia_h", "asia_l", "pd_h", "pd_l", "swing_h", "swing_l")),
        "swing": dict(interval="4h", rr=3.0, max_bars=6, disp_atr=1.0, kz=False, expiry=8,
                      sl_buf=0.2, swing_n=4, cooldown=6, bias_ema=50,
                      levels=("pd_h", "pd_l", "swing_h", "swing_l")),
    }

    def generate(self, df, symbol):
        p = self.preset
        n = len(df)
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        a = atr(df).to_numpy()
        lv = day_levels(df)
        lv["swing_h"], lv["swing_l"] = swing_points(df, p["swing_n"])
        L = {k: lv[k].to_numpy() for k in p["levels"]}
        kz = killzone_mask(df.index) if p["kz"] else np.ones(n, dtype=bool)
        bias = ema(df["close"], p["bias_ema"]).to_numpy() if p["bias_ema"] else None
        sess = session_labels(df.index)

        sigs: list[Signal] = []
        pending = None
        last = -10**9
        for i in range(60, n):
            if np.isnan(a[i]):
                continue
            if pending and i - pending["bar"] > p["max_bars"]:
                pending = None

            # 1) detect a liquidity sweep
            if pending is None and kz[i] and i - last > p["cooldown"]:
                for name, arr in L.items():
                    lvl = arr[i]
                    if np.isnan(lvl):
                        continue
                    if name.endswith("_h") and h[i] > lvl > c[i] and (bias is None or c[i] < bias[i]):
                        pending = dict(side=-1, bar=i, level=name)
                        break
                    if name.endswith("_l") and l[i] < lvl < c[i] and (bias is None or c[i] > bias[i]):
                        pending = dict(side=1, bar=i, level=name)
                        break

            # 2) wait for displacement + MSS + FVG after the sweep
            if pending and i >= pending["bar"] + 1:
                s, side = pending["bar"], pending["side"]
                if side == -1:
                    body = o[i - 1] - c[i - 1]
                    fvg = l[i - 2] > h[i]
                    mss = c[i - 1] < l[max(s - 3, 0): i - 1].min()
                    if fvg and mss and body >= p["disp_atr"] * a[i - 1]:
                        entry = (l[i - 2] + h[i]) / 2
                        slp = h[s: i + 1].max() + p["sl_buf"] * a[i]
                        risk = slp - entry
                        if entry > c[i] and 0.3 * a[i] <= risk <= 4 * a[i]:
                            sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, -1, entry, slp,
                                               entry - p["rr"] * risk, p["rr"], "limit", p["expiry"],
                                               f"{pending['level']} sweep -> displacement + FVG (short)", sess[i]))
                            last, pending = i, None
                else:
                    body = c[i - 1] - o[i - 1]
                    fvg = h[i - 2] < l[i]
                    mss = c[i - 1] > h[max(s - 3, 0): i - 1].max()
                    if fvg and mss and body >= p["disp_atr"] * a[i - 1]:
                        entry = (h[i - 2] + l[i]) / 2
                        slp = l[s: i + 1].min() - p["sl_buf"] * a[i]
                        risk = entry - slp
                        if entry < c[i] and 0.3 * a[i] <= risk <= 4 * a[i]:
                            sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, 1, entry, slp,
                                               entry + p["rr"] * risk, p["rr"], "limit", p["expiry"],
                                               f"{pending['level']} sweep -> displacement + FVG (long)", sess[i]))
                            last, pending = i, None
        return sigs


class TrendPull(Strategy):
    name = "trendpull"
    PRESETS = {
        "swing": dict(interval="4h", rr=3.0, fast=21, mid=50, slow=200, cooldown=6),
        "scalp": dict(interval="15m", rr=2.0, fast=21, mid=50, slow=200, cooldown=8),
    }

    def generate(self, df, symbol):
        p = self.preset
        n = len(df)
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        a = atr(df).to_numpy()
        e1 = ema(df["close"], p["fast"]).to_numpy()
        e2 = ema(df["close"], p["mid"]).to_numpy()
        e3 = ema(df["close"], p["slow"]).to_numpy()
        sess = session_labels(df.index)
        sigs: list[Signal] = []
        last = -10**9
        for i in range(p["slow"] + 10, n):
            if i - last <= p["cooldown"] or np.isnan(a[i]):
                continue
            up = c[i] > e3[i] and e2[i] > e3[i] and e2[i] > e2[i - 5]
            dn = c[i] < e3[i] and e2[i] < e3[i] and e2[i] < e2[i - 5]
            if up and (l[i - 3: i + 1] <= e1[i]).any() and c[i] > h[i - 1] and c[i] > o[i]:
                sl = l[i - 4: i + 1].min() - 0.1 * a[i]
                risk = c[i] - sl
                if risk > 3.5 * a[i]:
                    continue
                if risk < 0.8 * a[i]:
                    sl, risk = c[i] - 0.8 * a[i], 0.8 * a[i]
                sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, 1, c[i], sl,
                                   c[i] + p["rr"] * risk, p["rr"], "market", 0,
                                   "Uptrend pullback to EMA21 + break of prior high", sess[i]))
                last = i
            elif dn and (h[i - 3: i + 1] >= e1[i]).any() and c[i] < l[i - 1] and c[i] < o[i]:
                sl = h[i - 4: i + 1].max() + 0.1 * a[i]
                risk = sl - c[i]
                if risk > 3.5 * a[i]:
                    continue
                if risk < 0.8 * a[i]:
                    sl, risk = c[i] + 0.8 * a[i], 0.8 * a[i]
                sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, -1, c[i], sl,
                                   c[i] - p["rr"] * risk, p["rr"], "market", 0,
                                   "Downtrend pullback to EMA21 + break of prior low", sess[i]))
                last = i
        return sigs


class SetForget(Strategy):
    """Daily price action, 'place the order and walk away'.
    Pin bar / engulfing candle rejecting a confirmed swing support/resistance, with the bigger trend
    (SMA50/SMA200) on its side. Pin bar: limit at the candle midpoint; engulfing: market at the close.
    SL beyond the wick, TP at 2R, then no tinkering."""
    name = "setforget"
    PRESETS = {"daily": dict(interval="1d", rr=2.0, swing_n=4, touch_atr=0.5, expiry=4, cooldown=3, level_age=150)}

    def generate(self, df, symbol):
        p = self.preset
        n = len(df)
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        a = atr(df).to_numpy()
        s50 = df["close"].rolling(50).mean().to_numpy()
        s200 = df["close"].rolling(200).mean().to_numpy()
        hi, lo = pivot_idx(h, p["swing_n"], "max"), pivot_idx(l, p["swing_n"], "min")
        sess = session_labels(df.index)
        sigs: list[Signal] = []
        last = -10**9
        for i in range(210, n):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or np.isnan(s200[i]):
                continue
            rng, body = h[i] - l[i], abs(c[i] - o[i])
            if rng < 0.8 * a[i] or rng <= 0:
                continue
            lower, upper = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
            pin_up = lower / rng >= 0.6 and body / rng <= 0.35
            pin_dn = upper / rng >= 0.6 and body / rng <= 0.35
            eng_up = c[i - 1] < o[i - 1] and c[i] > o[i] and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body >= 0.8 * a[i]
            eng_dn = c[i - 1] > o[i - 1] and c[i] < o[i] and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body >= 0.8 * a[i]
            ok_conf = lambda j: j <= i - p["swing_n"] and i - j <= p["level_age"]  # pivot confirmed, not too old
            if (pin_up or eng_up) and (c[i] > s200[i] or s50[i] > s50[i - 10]):
                near = [l[j] for j in lo if ok_conf(j) and abs(l[i] - l[j]) <= p["touch_atr"] * a[i]]
                if near:
                    lvl = min(near)
                    entry, typ = ((h[i] + l[i]) / 2, "limit") if pin_up else (c[i], "market")
                    sl = min(l[i], lvl) - 0.1 * a[i]
                    risk = entry - sl
                    if 0.5 * a[i] <= risk <= 3 * a[i]:
                        sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, 1, entry, sl, entry + p["rr"] * risk,
                                           p["rr"], typ, p["expiry"],
                                           f"{'Pin bar' if pin_up else 'Bullish engulfing'} rejecting support {lvl:.5f} on the daily", sess[i]))
                        last = i
                        continue
            if (pin_dn or eng_dn) and (c[i] < s200[i] or s50[i] < s50[i - 10]):
                near = [h[j] for j in hi if ok_conf(j) and abs(h[i] - h[j]) <= p["touch_atr"] * a[i]]
                if near:
                    lvl = max(near)
                    entry, typ = ((h[i] + l[i]) / 2, "limit") if pin_dn else (c[i], "market")
                    sl = max(h[i], lvl) + 0.1 * a[i]
                    risk = sl - entry
                    if 0.5 * a[i] <= risk <= 3 * a[i]:
                        sigs.append(Signal(i, df.index[i], symbol, self.name, self.mode, -1, entry, sl, entry - p["rr"] * risk,
                                           p["rr"], typ, p["expiry"],
                                           f"{'Pin bar' if pin_dn else 'Bearish engulfing'} rejecting resistance {lvl:.5f} on the daily", sess[i]))
                        last = i
        return sigs


STRATEGIES = {"ict": ICT, "trendpull": TrendPull, "setforget": SetForget}
