"""Mean-reversion strategies: 2-period RSI and Bollinger band reversion in a ranging market."""
from __future__ import annotations

import numpy as np

from ..indicators import bollinger, ema, rsi
from ..regime import RANGING, TRENDING, VOLATILE
from .base import Strategy


class RSI2(Strategy):
    """2-period RSI. The widely documented version is mean reversion: in an uptrend (price above the 200 EMA) buy when
    RSI(2) closes below 10, in a downtrend sell when it closes above 90.
    'Profitable Trading Strategies' (S9) describes the OPPOSITE direction (RSI > 90 = buy), which is a momentum
    reading and conflicts with that usage. Both are supported: preset `direction` = "revert" (default) or "momentum".
    Neither is validated here, so check `validate` before trusting either. Stop 2 ATR, target 1.2R (reversion trades
    are short-lived), no trading in a volatile market."""
    name = "rsi2"
    label = "RSI(2) extremes"
    family = "mean_reversion"
    style = "swing"
    summary = "RSI(2) below 10 in an uptrend (above 90 in a downtrend); the book's momentum reading is available as an option."
    PRESETS = {
        "swing": dict(interval="4h", rr=1.2, lo=10, hi=90, trend=200, atr_stop=2.0, cooldown=4, direction="revert"),
        "daily": dict(interval="1d", rr=1.2, lo=10, hi=90, trend=200, atr_stop=2.0, cooldown=3, direction="revert"),
        "scalp": dict(interval="15m", rr=1.0, lo=8, hi=92, trend=200, atr_stop=1.5, cooldown=6, direction="revert"),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        c = df["close"].to_numpy()
        r2 = rsi(df["close"], 2).to_numpy()
        tr = ema(df["close"], p["trend"]).to_numpy()
        sigs, last = [], -10**9
        for i in range(p["trend"] + 5, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or np.isnan(r2[i]) or reg[i] == VOLATILE:
                continue
            lo_hit, hi_hit = r2[i] < p["lo"], r2[i] > p["hi"]
            if p["direction"] == "revert":
                side = 1 if (lo_hit and c[i] > tr[i]) else -1 if (hi_hit and c[i] < tr[i]) else 0
                why = f"RSI(2) {r2[i]:.0f} is stretched {'down' if side == 1 else 'up'} inside a {'up' if side == 1 else 'down'}trend (price vs EMA{p['trend']})"
            else:  # the book's reading: strength = buy, weakness = sell
                side = 1 if (hi_hit and c[i] > tr[i]) else -1 if (lo_hit and c[i] < tr[i]) else 0
                why = f"RSI(2) {r2[i]:.0f} shows momentum {'up' if side == 1 else 'down'} (book version)"
            if not side:
                continue
            sg = self.make(df, i, symbol, side, c[i], c[i] - side * p["atr_stop"] * a[i], a=a, sess=sess, reg=reg,
                           rr=p["rr"], max_stop=4, reason=why,
                           invalidation="Invalid if price closes beyond the stop or RSI(2) returns to the middle (35-65)")
            if sg:
                sigs.append(sg)
                last = i
        return sigs


class BollingerReversion(Strategy):
    """Range trading with Bollinger Bands (20, 2). ONLY when the regime is ranging (ADX < 20): price pokes outside
    the outer band and closes back inside, with RSI(14) confirming (below 40 for buys, above 60 for sells).
    Entry at that close, stop 0.3 ATR beyond the extreme, target the middle band (needs at least 1R).
    It does not trade trending or volatile markets, where fading the bands is how accounts get hurt."""
    name = "bbrevert"
    label = "Bollinger range reversion"
    family = "mean_reversion"
    style = "intraday"
    summary = "In a ranging market, a close back inside the outer band targets the middle band."
    PRESETS = {
        "scalp": dict(interval="15m", cooldown=6, min_rr=1.0),
        "swing": dict(interval="4h", cooldown=5, min_rr=1.0),
        "daily": dict(interval="1d", cooldown=4, min_rr=1.0),
    }

    def generate(self, df, symbol):
        p = self.preset
        a, sess, reg = self.context(df)
        h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
        mid, up, lo, _ = (x.to_numpy() for x in bollinger(df["close"]))
        r14 = rsi(df["close"], 14).to_numpy()
        sigs, last = [], -10**9
        for i in range(60, len(df)):
            if i - last <= p["cooldown"] or np.isnan(a[i]) or np.isnan(mid[i]) or reg[i] != RANGING:
                continue
            if l[i] < lo[i] and c[i] > lo[i] and r14[i] < 40:
                side, sl, tp = 1, l[i] - 0.3 * a[i], mid[i]
            elif h[i] > up[i] and c[i] < up[i] and r14[i] > 60:
                side, sl, tp = -1, h[i] + 0.3 * a[i], mid[i]
            else:
                continue
            sg = self.make(df, i, symbol, side, c[i], sl, a=a, sess=sess, reg=reg, tp=tp, max_stop=3,
                           reason=f"Ranging market: poked {'below the lower' if side == 1 else 'above the upper'} Bollinger band and closed back inside, RSI {r14[i]:.0f}",
                           invalidation="Invalid if the market starts trending (ADX >= 25) or price closes beyond the stop")
            if sg:
                sigs.append(sg)
                last = i
        return sigs
