"""Trend-following strategies: EMA pullback and the EMA 20/60/100 crossover."""
from __future__ import annotations

import numpy as np

from ..indicators import atr, ema
from ..regime import RANGING, VOLATILE, regime_series
from ..sessions import session_labels
from .base import Signal, Strategy


class TrendPull(Strategy):
    name = "trendpull"
    label = "Trend pullback"
    family = "trend"
    style = "intraday"
    summary = "Trade with the trend after a pullback to the 21 EMA and a break of the prior candle."
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
        reg = regime_series(df)
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
                                   "Uptrend pullback to EMA21 + break of prior high", sess[i], self.family,
                                   p["interval"], str(reg[i]), "Invalid if price closes below the stop-loss"))
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
                                   "Downtrend pullback to EMA21 + break of prior low", sess[i], self.family,
                                   p["interval"], str(reg[i]), "Invalid if price closes above the stop-loss"))
                last = i
        return sigs


class EmaCross(Strategy):
    """EMA 20 / 60 crossover with the 100 EMA as the trend line (rules from 'Profitable Trading Strategies', S2).

    Buy when EMA20 crosses above EMA60 while price is above EMA100; sell on the mirror image.
    The book gives no stop or target and calls the 100 EMA only a 'trend line': the ATR stop, the fixed
    reward-to-risk and the hard 100 EMA filter are this project's additions. No trades in a ranging or volatile market
    (the book says to avoid sideways markets but gives no number, so ADX < 20 is used)."""
    name = "emacross"
    label = "EMA 20/60/100 cross"
    family = "trend"
    style = "swing"
    summary = "EMA20 crosses EMA60 in the direction of the EMA100, ATR stop, fixed RR."
    PRESETS = {
        "swing": dict(interval="4h", rr=2.0, fast=20, slow=60, trend=100, atr_stop=2.0, cooldown=6),
        "daily": dict(interval="1d", rr=2.0, fast=20, slow=60, trend=100, atr_stop=2.0, cooldown=5),
        "scalp": dict(interval="15m", rr=1.5, fast=20, slow=60, trend=100, atr_stop=2.0, cooldown=12),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        c = df["close"]
        f, s, t = (ema(c, p[k]).to_numpy() for k in ("fast", "slow", "trend"))
        cc = c.to_numpy()
        sigs, last = [], -10**9
        for i in range(p["trend"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or reg[i] in (RANGING, VOLATILE):
                continue
            up = f[i - 1] <= s[i - 1] and f[i] > s[i] and cc[i] > t[i]
            dn = f[i - 1] >= s[i - 1] and f[i] < s[i] and cc[i] < t[i]
            if not (up or dn):
                continue
            side = 1 if up else -1
            sl = cc[i] - side * p["atr_stop"] * a[i]
            sg = self.make(df, i, symbol, side, cc[i], sl, a=a, sess=sess, reg=reg, rr=p["rr"], min_stop=0.5, max_stop=4,
                           reason=f"EMA{p['fast']} crossed {'above' if up else 'below'} EMA{p['slow']} with price {'above' if up else 'below'} EMA{p['trend']}",
                           invalidation=f"Invalid if EMA{p['fast']} crosses back through EMA{p['slow']} or price closes beyond the stop")
            if sg:
                sigs.append(sg)
                last = i
        return sigs
