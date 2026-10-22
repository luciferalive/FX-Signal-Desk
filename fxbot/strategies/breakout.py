"""Breakout strategies: Donchian channel breakout, breakout + retest (role reversal), Bollinger squeeze, NR7."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import bollinger, donchian, ema
from ..regime import RANGING, VOLATILE
from .base import Strategy


class Donchian(Strategy):
    """Turtle-style channel breakout. Buy when a bar CLOSES above the highest high of the previous N bars (sell
    below the lowest low), with the 100 EMA on the trade's side and ADX not in a dead-flat range. Stop 2 ATR,
    target 2R. A breakout system loses often in ranges: the regime filter is there to cut that, not remove it."""
    name = "donchian"
    label = "Donchian breakout"
    family = "breakout"
    style = "swing"
    summary = "Close beyond the previous N-bar high/low, trend EMA on its side, ATR stop."
    PRESETS = {
        "swing": dict(interval="4h", rr=2.0, n=20, trend=100, atr_stop=2.0, cooldown=8),
        "daily": dict(interval="1d", rr=2.0, n=20, trend=100, atr_stop=2.0, cooldown=6),
        "scalp": dict(interval="15m", rr=1.5, n=32, trend=100, atr_stop=1.8, cooldown=16),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        c = df["close"].to_numpy()
        hi, lo = (x.to_numpy() for x in donchian(df, p["n"]))
        tr = ema(df["close"], p["trend"]).to_numpy()
        sigs, last = [], -10**9
        for i in range(p["trend"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or np.isnan(hi[i]) or reg[i] in (RANGING, VOLATILE):
                continue
            if c[i] > hi[i] and c[i] > tr[i]:
                side, lvl = 1, hi[i]
            elif c[i] < lo[i] and c[i] < tr[i]:
                side, lvl = -1, lo[i]
            else:
                continue
            sg = self.make(df, i, symbol, side, c[i], c[i] - side * p["atr_stop"] * a[i], a=a, sess=sess, reg=reg, rr=p["rr"],
                           max_stop=4, reason=f"Closed {'above' if side == 1 else 'below'} the {p['n']}-bar {'high' if side == 1 else 'low'} {lvl:.5f}",
                           invalidation=f"Invalid if price closes back inside the channel at {lvl:.5f}")
            if sg:
                sigs.append(sg)
                last = i
        return sigs


class BreakoutRetest(Strategy):
    """Breakout and retest (role reversal). A bar closes beyond the previous N-bar extreme; within `wait` bars
    price pulls back to that level (within 0.3 ATR) and a candle closes back in the breakout direction. Entry at
    that close, stop beyond the retest extreme, target 2R. Skipped if price already closed back through the level."""
    name = "retest"
    label = "Breakout + retest"
    family = "breakout"
    style = "intraday"
    summary = "Break of the recent high/low, pullback to the broken level, bounce candle, entry."
    PRESETS = {
        "scalp": dict(interval="15m", rr=2.0, n=32, wait=10, tol=0.3, cooldown=10),
        "swing": dict(interval="4h", rr=2.0, n=24, wait=8, tol=0.3, cooldown=8),
        "daily": dict(interval="1d", rr=2.0, n=20, wait=6, tol=0.3, cooldown=5),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        hi, lo = (x.to_numpy() for x in donchian(df, p["n"]))
        sigs, last = [], -10**9
        for i in range(p["n"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or reg[i] == VOLATILE:
                continue
            for k in range(2, p["wait"] + 1):  # breakout bar j = i-k, retest happens after it
                j = i - k
                if np.isnan(hi[j]):
                    continue
                if c[j] > hi[j]:
                    lvl = hi[j]
                    seg_l = l[j + 1: i + 1]
                    if (c[j + 1: i + 1] < lvl).any() or seg_l.min() > lvl + p["tol"] * a[i]:
                        continue  # failed back through, or never came back to the level
                    if c[i] > o[i] and c[i] > h[i - 1]:
                        sg = self.make(df, i, symbol, 1, c[i], seg_l.min() - 0.2 * a[i], a=a, sess=sess, reg=reg, rr=p["rr"],
                                       reason=f"Broke {lvl:.5f}, retested it as support and bounced",
                                       invalidation=f"Invalid if price closes back below {lvl:.5f}")
                        if sg:
                            sigs.append(sg)
                            last = i
                            break
                elif c[j] < lo[j]:
                    lvl = lo[j]
                    seg_h = h[j + 1: i + 1]
                    if (c[j + 1: i + 1] > lvl).any() or seg_h.max() < lvl - p["tol"] * a[i]:
                        continue
                    if c[i] < o[i] and c[i] < l[i - 1]:
                        sg = self.make(df, i, symbol, -1, c[i], seg_h.max() + 0.2 * a[i], a=a, sess=sess, reg=reg, rr=p["rr"],
                                       reason=f"Broke {lvl:.5f}, retested it as resistance and rejected",
                                       invalidation=f"Invalid if price closes back above {lvl:.5f}")
                        if sg:
                            sigs.append(sg)
                            last = i
                            break
        return sigs


class BollingerSqueeze(Strategy):
    """Volatility squeeze breakout ('Profitable Trading Strategies', S7, adapted).
    The book squeezes on BandWidth near 0.0100, which is specific to EURUSD H4; this uses a relative rule instead:
    BandWidth (20, 2) within the lowest 20% of its last 120 bars during any of the previous 6 bars. Then a close
    outside the band (and beyond the 20 SMA) starts the trade. Stop at the previous candle's far side (min 0.8 ATR),
    target 2R."""
    name = "squeeze"
    label = "Bollinger squeeze"
    family = "breakout"
    style = "swing"
    summary = "Band width compresses to a low, then a close outside the band trades the expansion."
    PRESETS = {
        "swing": dict(interval="4h", rr=2.0, pct=0.2, look=120, cooldown=8),
        "daily": dict(interval="1d", rr=2.0, pct=0.2, look=120, cooldown=6),
        "scalp": dict(interval="15m", rr=1.6, pct=0.2, look=160, cooldown=16),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        c, h, l = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
        mid, up, lo, bw = bollinger(df["close"])
        mid, up, lo, bw = mid.to_numpy(), up.to_numpy(), lo.to_numpy(), bw.to_numpy()
        thr = pd.Series(bw).rolling(p["look"]).quantile(p["pct"]).to_numpy()
        squeezed = bw <= thr
        sigs, last = [], -10**9
        for i in range(p["look"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or reg[i] == VOLATILE:
                continue
            if not squeezed[i - 6: i].any():
                continue
            if c[i] > up[i] and c[i] > mid[i]:
                side, sl = 1, min(l[i - 1], c[i] - 0.8 * a[i])
            elif c[i] < lo[i] and c[i] < mid[i]:
                side, sl = -1, max(h[i - 1], c[i] + 0.8 * a[i])
            else:
                continue
            sg = self.make(df, i, symbol, side, c[i], sl, a=a, sess=sess, reg=reg, rr=p["rr"], max_stop=3.5,
                           reason=f"Bollinger BandWidth squeezed, then price closed {'above the upper' if side == 1 else 'below the lower'} band",
                           invalidation="Invalid if price closes back inside the bands through the 20 SMA")
            if sg:
                sigs.append(sg)
                last = i
        return sigs


class NR7(Strategy):
    """Narrow Range 7 breakout ('Profitable Trading Strategies', S8). The narrowest candle of the last 7 sets up a
    break of its high or low. The book uses stop orders; this version enters at the CLOSE of the first bar (within
    3 bars) that closes beyond the NR7 candle, only in the direction of the 50 EMA slope, stop at the NR7 candle's
    opposite side, target 2R."""
    name = "nr7"
    label = "NR7 breakout"
    family = "breakout"
    style = "swing"
    summary = "Narrowest range of 7 bars, then a close beyond it in the trend direction."
    PRESETS = {
        "swing": dict(interval="4h", rr=2.0, n=7, wait=3, cooldown=6),
        "daily": dict(interval="1d", rr=2.0, n=7, wait=3, cooldown=4),
        "scalp": dict(interval="15m", rr=1.5, n=7, wait=3, cooldown=10),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
        rng = h - l
        e50 = ema(df["close"], 50).to_numpy()
        sigs, last = [], -10**9
        for i in range(60, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or reg[i] == VOLATILE:
                continue
            for k in range(1, p["wait"] + 1):
                j = i - k  # candidate narrow-range bar
                if rng[j] > rng[j - p["n"] + 1: j + 1].min() or rng[j] <= 0:
                    continue
                if (c[j + 1: i] > h[j]).any() or (c[j + 1: i] < l[j]).any():
                    continue  # already broke out earlier
                if c[i] > h[j] and e50[i] > e50[i - 5]:
                    side, sl = 1, l[j] - 0.1 * a[i]
                elif c[i] < l[j] and e50[i] < e50[i - 5]:
                    side, sl = -1, h[j] + 0.1 * a[i]
                else:
                    continue
                sg = self.make(df, i, symbol, side, c[i], sl, a=a, sess=sess, reg=reg, rr=p["rr"], max_stop=3.5,
                               reason=f"NR{p['n']} candle (narrowest of {p['n']}) broke {'up' if side == 1 else 'down'} with the 50 EMA",
                               invalidation="Invalid if price closes back inside the narrow-range candle")
                if sg:
                    sigs.append(sg)
                    last = i
                    break
        return sigs
