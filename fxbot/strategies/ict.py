"""ICT / smart-money strategy: liquidity sweep -> displacement + market structure shift + FVG -> limit entry.

Entry: limit at the fair-value-gap midpoint after the sweep. SL beyond the sweep extreme. TP = RR x risk.
Scalp mode only trades inside the London / NY-AM killzones; swing mode runs on 4H with an EMA bias filter.
This is a mechanical simplification of ICT concepts, not a full copy of anyone's model.
"""
from __future__ import annotations

import numpy as np

from ..indicators import atr, ema, swing_points
from ..regime import regime_series
from ..sessions import day_levels, killzone_mask, session_labels
from .base import Signal, Strategy


class ICT(Strategy):
    name = "ict"
    label = "ICT sweep + FVG"
    family = "ict"
    style = "scalp"
    summary = "Liquidity sweep, displacement with structure shift, limit entry at the fair value gap."
    requires = ("OHLC candles", "sessions (New York time)")
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
        reg = regime_series(df)

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
                                               f"{pending['level']} sweep -> displacement + FVG (short)", sess[i], self.family, p["interval"], str(reg[i]),
                                               "Invalid if price closes above the sweep high before the limit fills"))
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
                                               f"{pending['level']} sweep -> displacement + FVG (long)", sess[i], self.family, p["interval"], str(reg[i]),
                                               "Invalid if price closes below the sweep low before the limit fills"))
                            last, pending = i, None
        return sigs
