"""Risk management. Signal generation never executes anything; this module only says whether a trade is acceptable.

validate(...) returns (ok, reasons). A signal is rejected when ANY rule fails, no matter how many strategies agree.
"""
from __future__ import annotations

import pandas as pd

from .backtest import pip_size
from .config import spread_pips


def pip_value_per_lot(symbol: str, price: float) -> tuple[float, bool]:
    """Approximate USD value of one pip for one standard lot, and whether the number is exact enough.
    USD-quoted pairs are exact ($10). USD-base pairs use the current price. Crosses need another rate, so they
    return an approximation (flagged False): check your broker's calculator before sizing."""
    s = symbol.upper().replace("/", "")
    pip = pip_size(s)
    if s.startswith(("XAU", "XAG")):
        return (10.0 if s.startswith("XAU") else 50.0), True  # 100 oz and 5000 oz contracts
    if s.endswith("USD"):
        return 10.0, True
    if s.startswith("USD"):
        return 100000 * pip / price, True
    return 10.0, False


def position_size(account: float, risk_pct: float, entry: float, sl: float, symbol: str) -> dict:
    """Lots so that hitting the stop loses about risk_pct of the account."""
    pip = pip_size(symbol)
    stop_pips = abs(entry - sl) / pip
    if stop_pips <= 0:
        return dict(lots=0.0, risk_amount=0.0, stop_pips=0.0, exact=False)
    value, exact = pip_value_per_lot(symbol, entry)
    risk_amount = account * risk_pct / 100
    lots = risk_amount / (stop_pips * value)
    return dict(lots=round(lots, 2), risk_amount=round(risk_amount, 2), stop_pips=round(stop_pips, 1), exact=exact)


def atr_stop(entry: float, atr_value: float, side: int, mult: float = 1.5) -> float:
    """ATR-based stop-loss option."""
    return entry - side * mult * atr_value


def validate(sig: dict, atr_value: float, symbol: str, cfg: dict, session: str | None = None,
             vol_ratio: float | None = None, family: str | None = None) -> tuple[bool, list[str]]:
    """sig needs: side (+1/-1), entry, sl, tp (final target). Returns (ok, reasons it failed)."""
    r, why = cfg["risk"], []
    side, entry, sl, tp = sig["side"], sig["entry"], sig["sl"], sig["tp"]
    pip = pip_size(symbol)
    risk = (entry - sl) * side
    if risk <= 0:
        return False, ["stop-loss is on the wrong side of the entry"]
    if (tp - entry) * side <= 0:
        return False, ["take-profit is on the wrong side of the entry"]
    stop_atr = risk / atr_value if atr_value else 0
    if stop_atr < r["stop_atr_min"]:
        why.append(f"stop is only {stop_atr:.2f} ATR away (needs {r['stop_atr_min']}+), it would be hit by noise")
    if stop_atr > r["stop_atr_max"]:
        why.append(f"stop is {stop_atr:.1f} ATR away (max {r['stop_atr_max']}), too wide")
    rr = (tp - entry) * side / risk
    min_rr = r.get("min_rr_family", {}).get(family or sig.get("family"), r["min_rr"])
    if rr < min_rr - 1e-9:
        why.append(f"reward-to-risk {rr:.2f} is below the minimum {min_rr}")
    spread = spread_pips(cfg, symbol) * pip
    if spread > r["max_spread_to_risk"] * risk:
        why.append(f"spread {spread / pip:.1f} pips is {100 * spread / risk:.0f}% of the stop "
                   f"(max {100 * r['max_spread_to_risk']:.0f}%)")
    if vol_ratio is not None:
        if vol_ratio > r["vol_ratio_max"]:
            why.append(f"volatility is {vol_ratio:.1f}x normal (max {r['vol_ratio_max']}x)")
        elif vol_ratio < r["vol_ratio_min"]:
            why.append(f"volatility is only {vol_ratio:.2f}x normal, the market is dead")
    if r.get("sessions") and session is not None and session not in r["sessions"]:
        why.append(f"outside the allowed sessions ({', '.join(r['sessions'])})")
    if r["risk_pct"] > r["max_risk_pct"]:
        why.append(f"risk per trade {r['risk_pct']}% is above the maximum {r['max_risk_pct']}%")
    return (not why), why


def dedupe(signals: list, window: int) -> list:
    """One signal per (strategy, side) inside `window` bars; the first one wins. Works on strategy Signal objects."""
    out, last = [], {}
    for s in sorted(signals, key=lambda x: x.idx):
        k = (s.strategy, s.side)
        if k in last and s.idx - last[k] <= window:
            continue
        last[k] = s.idx
        out.append(s)
    return out


def invalidated(sig, df: pd.DataFrame) -> str | None:
    """Why a signal is no longer valid given the candles AFTER it (None = still valid)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    seg_h, seg_l = h[sig.idx + 1:], l[sig.idx + 1:]
    if sig.side == 1 and (seg_l <= sig.sl).any():
        return "stop-loss level was already touched"
    if sig.side == -1 and (seg_h >= sig.sl).any():
        return "stop-loss level was already touched"
    if sig.entry_type == "limit":
        if sig.side == 1 and (seg_h >= sig.tp).any() or sig.side == -1 and (seg_l <= sig.tp).any():
            return "target was reached before the limit entry filled"
    return None
