"""Price-action strategies: pin bar / engulfing at swing support or resistance (Set & Forget style)
and failed breakouts."""
from __future__ import annotations

import numpy as np

from ..indicators import atr, pivot_idx
from ..regime import regime_series
from ..sessions import session_labels
from .base import Signal, Strategy


class SetForget(Strategy):
    """Daily price action, 'place the order and walk away'.
    Pin bar / engulfing candle rejecting a confirmed swing support/resistance, with the bigger trend
    (SMA50/SMA200) on its side. Pin bar: limit at the candle midpoint; engulfing: market at the close.
    SL beyond the wick, TP at 2R, then no tinkering."""
    name = "setforget"
    label = "Pin bar / engulfing at swing level"
    family = "price_action"
    style = "swing"
    summary = "Daily pin bar or engulfing candle rejecting a confirmed swing level, trend on its side."
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
        reg = regime_series(df)
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
                                           f"{'Pin bar' if pin_up else 'Bullish engulfing'} rejecting support {lvl:.5f} on the daily", sess[i], self.family, p["interval"], str(reg[i]),
                                           "Invalid if price closes beyond the wick extreme"))
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
                                           f"{'Pin bar' if pin_dn else 'Bearish engulfing'} rejecting resistance {lvl:.5f} on the daily", sess[i], self.family, p["interval"], str(reg[i]),
                                           "Invalid if price closes beyond the wick extreme"))
                        last = i
        return sigs


class FailedBreakout(Strategy):
    """Fade a breakout that fails: price closes beyond the last N-bar extreme, then closes back inside within
    a few bars. Entry at the close that re-enters the range, stop beyond the failed extreme, target 2R.
    Does not trade a strongly trending market (ADX >= 25 usually means breakouts hold)."""
    name = "failbreak"
    label = "Failed breakout fade"
    family = "price_action"
    style = "intraday"
    summary = "Breakout beyond the recent range that closes back inside is faded."
    PRESETS = {
        "scalp": dict(interval="15m", rr=1.8, n=32, back=3, cooldown=8),
        "swing": dict(interval="4h", rr=2.0, n=30, back=3, cooldown=6),
        "daily": dict(interval="1d", rr=2.0, n=20, back=3, cooldown=4),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
        hh = df["high"].shift(1).rolling(p["n"]).max().to_numpy()  # extremes known BEFORE each bar
        ll = df["low"].shift(1).rolling(p["n"]).min().to_numpy()
        sigs, last = [], -10**9
        for i in range(p["n"] + p["back"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or reg[i] == "trending":
                continue
            for k in range(1, p["back"] + 1):  # the breakout bar was k bars ago
                j = i - k
                if np.isnan(hh[j]):
                    continue
                if c[j] > hh[j] and c[i] < hh[j] and not (c[j + 1: i] < hh[j]).any():
                    # broke the high, then stayed above it until THIS bar closed back inside
                    sg = self.make(df, i, symbol, -1, c[i], h[j: i + 1].max() + 0.15 * a[i], a=a, sess=sess, reg=reg,
                                   rr=p["rr"], reason=f"Broke the {p['n']}-bar high {hh[j]:.5f}, then closed back below it (failed breakout)",
                                   invalidation="Invalid if price closes above the failed-breakout high")
                elif c[j] < ll[j] and c[i] > ll[j] and not (c[j + 1: i] > ll[j]).any():
                    sg = self.make(df, i, symbol, 1, c[i], l[j: i + 1].min() - 0.15 * a[i], a=a, sess=sess, reg=reg,
                                   rr=p["rr"], reason=f"Broke the {p['n']}-bar low {ll[j]:.5f}, then closed back above it (failed breakout)",
                                   invalidation="Invalid if price closes below the failed-breakout low")
                else:
                    continue
                if sg:
                    sigs.append(sg)
                    last = i
                break
        return sigs
