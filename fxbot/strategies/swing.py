"""Swing strategies: Heikin-Ashi colour change with stochastic confirmation (4H / daily)."""
from __future__ import annotations

import numpy as np

from ..indicators import heikin_ashi, stochastic
from ..regime import VOLATILE
from .base import Strategy


class HeikinStoch(Strategy):
    """Heikin-Ashi + Stochastic (14, 7, 3) ('Profitable Trading Strategies', S3).
    After a run of at least 3 candles of one colour, two consecutive Heikin-Ashi candles of the opposite colour mark a
    turn. The book contradicts itself on the stochastic zones (70/30 in one place, inverted in another); this version
    takes the sensible reading: longs only when %K was in the lower area (<= 40) at the end of the down run, shorts
    when it was in the upper area (>= 60). Entry at the close of the second candle (the book uses a stop order a few
    pips beyond it). Stop beyond the last 5 bars' extreme, target 2R; the book's 100-pip stop and break-even at +50
    pips are not used because pips mean different things on different pairs."""
    name = "hastoch"
    label = "Heikin-Ashi turn + stochastic"
    family = "swing"
    style = "swing"
    summary = "Heikin-Ashi colour change after a run, with stochastic in the right zone."
    PRESETS = {
        "daily": dict(interval="1d", rr=2.0, run=3, lo=40, hi=60, cooldown=4),
        "swing": dict(interval="4h", rr=2.0, run=3, lo=40, hi=60, cooldown=6),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
        ha = heikin_ashi(df)
        col = np.sign((ha["close"] - ha["open"]).to_numpy())
        k, _ = stochastic(df)
        k = k.to_numpy()
        sigs, last = [], -10**9
        for i in range(40, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or np.isnan(k[i - 2]) or reg[i] == VOLATILE:
                continue
            run = col[i - 2 - p["run"] + 1: i - 1]
            if col[i] == col[i - 1] != 0 and (run == -col[i]).all():
                side = int(col[i])
                if (side == 1 and k[i - 2] > p["lo"]) or (side == -1 and k[i - 2] < p["hi"]):
                    continue
                sl = (l[i - 4: i + 1].min() - 0.1 * a[i]) if side == 1 else (h[i - 4: i + 1].max() + 0.1 * a[i])
                sg = self.make(df, i, symbol, side, c[i], sl, a=a, sess=sess, reg=reg, rr=p["rr"], max_stop=3.5,
                               reason=f"Heikin-Ashi turned {'up' if side == 1 else 'down'} after {p['run']}+ candles the other way, stochastic {k[i - 2]:.0f}",
                               invalidation="Invalid if a Heikin-Ashi candle of the old colour prints or price closes beyond the stop")
                if sg:
                    sigs.append(sg)
                    last = i
        return sigs
