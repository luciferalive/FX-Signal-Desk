"""Scalping strategies (15m): London session breakout of the Asian range."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..regime import VOLATILE
from ..sessions import NY, day_key, day_levels, window_mask
from .base import Strategy


class SessionBreakout(Strategy):
    """Asian-range breakout during the London / NY-AM killzones.
    The Asian range (19:00-00:00 New York) must be neither tiny nor huge compared with ATR. The first 15m bar that
    CLOSES beyond the range inside a killzone, and is not already stretched (within 1 ATR of the level), is the trade.
    Stop at the middle of the Asian range, target 1.5R, one trade per day and direction. Skipped in a volatile regime.
    Needs 15m (or finer) data with correct UTC timestamps."""
    name = "sessionbreak"
    label = "Asian range breakout"
    family = "scalping"
    style = "scalp"
    summary = "First close beyond the Asian range during London / NY AM, stop at the range middle."
    requires = ("OHLC candles", "sessions (New York time)")
    PRESETS = {"scalp": dict(interval="15m", rr=1.5, min_w=3.0, max_w=16.0, chase=1.0)}

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        c = df["close"].to_numpy()
        lv = day_levels(df)
        ah, al = lv["asia_h"].to_numpy(), lv["asia_l"].to_numpy()
        kz = window_mask(df.index, "London KZ") | window_mask(df.index, "NY AM KZ")
        key = day_key(df.index).to_numpy()
        done: set = set()
        sigs = []
        for i in range(60, len(df)):
            if not kz[i] or np.isnan(ah[i]) or np.isnan(al[i]) or np.isnan(a[i]) or reg[i] == VOLATILE:
                continue
            width = ah[i] - al[i]
            if not (p["min_w"] * a[i] <= width <= p["max_w"] * a[i]):
                continue
            mid = (ah[i] + al[i]) / 2
            for side, lvl in ((1, ah[i]), (-1, al[i])):
                if (key[i], side) in done:
                    continue
                if (c[i] - lvl) * side > 0 and (c[i] - lvl) * side <= p["chase"] * a[i]:
                    done.add((key[i], side))
                    sg = self.make(df, i, symbol, side, c[i], mid, a=a, sess=sess, reg=reg, rr=p["rr"], max_stop=8, min_stop=0.5,
                                   reason=f"Closed {'above' if side == 1 else 'below'} the Asian range {lvl:.5f} inside a killzone",
                                   invalidation="Invalid if price closes back inside the Asian range")
                    if sg:
                        sigs.append(sg)
        return sigs
