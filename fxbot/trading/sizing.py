"""Position sizing from the REAL account equity and the broker's own instrument specification.

lots = risk money / (loss per lot if the stop is hit, including spread, slippage and commission)
The volume is rounded DOWN to the broker's step and is never raised to reach a minimum: if the minimum tradable
volume would risk more than allowed, the trade is rejected instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .models import BrokerError, SymbolSpec


def floor_to_step(volume: float, step: float) -> float:
    """Round DOWN to a multiple of step without float noise (0.07 / 0.01 is 6.999... in plain floats)."""
    if step <= 0:
        raise ValueError("volume step must be positive")
    d = (Decimal(str(volume)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step))
    return float(d)


def is_valid_volume(volume: float, spec: SymbolSpec) -> bool:
    if volume < spec.volume_min - 1e-12 or volume > spec.volume_max + 1e-12:
        return False
    steps = Decimal(str(volume - spec.volume_min)) / Decimal(str(spec.volume_step))
    return abs(steps - steps.to_integral_value()) < Decimal("0.000001")


# ------------------------------------------------------------------ currency conversion and pip value
def convert_ccy(amount: float, frm: str, to: str, rates: dict[str, float]) -> float:
    """Convert using FX rates {'EURUSD': 1.10, ...}. Direct, inverse or through USD. Raises when no path exists:
    a wrong pip value would silently break the risk, so a missing rate is an error, never a guess."""
    frm, to = frm.upper(), to.upper()
    if frm == to:
        return amount
    if frm + to in rates:
        return amount * rates[frm + to]
    if to + frm in rates:
        return amount / rates[to + frm]
    if "USD" not in (frm, to):
        return convert_ccy(convert_ccy(amount, frm, "USD", rates), "USD", to, rates)
    raise BrokerError(f"no exchange rate to convert {frm} to {to}")


def tick_value_from_rates(contract_size: float, tick_size: float, quote_ccy: str, account_ccy: str,
                          rates: dict[str, float]) -> float:
    """Account-currency value of one tick for one lot: contract x tick (in the quote currency) converted."""
    return convert_ccy(contract_size * tick_size, quote_ccy, account_ccy, rates)


def pip_value_per_lot(spec: SymbolSpec) -> float:
    """Account-currency value of one pip for one lot, from the broker's tick value (not a fixed $10)."""
    return spec.tick_value * spec.pip_size / spec.tick_size


# ------------------------------------------------------------------ sizing
@dataclass
class SizeResult:
    ok: bool
    volume: float = 0.0
    reason: str = ""
    risk_money_target: float = 0.0
    loss_per_lot: float = 0.0       # money lost per lot if the stop is hit, costs included
    risk_money: float = 0.0         # money actually risked at the final volume
    stop_pips: float = 0.0


def loss_per_lot(entry: float, sl: float, spec: SymbolSpec, slippage_price: float = 0.0) -> float:
    """Loss of one lot when the stop is hit: stop distance + expected slippage (ticks x tick value) + commission.
    `entry` must be the price the order really fills at (ask for a buy, bid for a sell), so the spread is inside it."""
    ticks = (abs(entry - sl) + max(slippage_price, 0.0)) / spec.tick_size
    return ticks * spec.tick_value + spec.commission_per_lot


def size_position(equity: float, risk_pct: float, entry: float, sl: float, spec: SymbolSpec,
                  slippage_pips: float = 0.0) -> SizeResult:
    if equity <= 0:
        return SizeResult(False, reason="account equity is zero or negative")
    if not math.isfinite(entry) or not math.isfinite(sl) or abs(entry - sl) < spec.tick_size / 2:
        return SizeResult(False, reason="stop-loss distance is invalid")
    target = equity * risk_pct / 100.0
    lpl = loss_per_lot(entry, sl, spec, slippage_pips * spec.pip_size)
    res = SizeResult(False, risk_money_target=round(target, 2), loss_per_lot=round(lpl, 4),
                     stop_pips=round(abs(entry - sl) / spec.pip_size, 1))
    raw = target / lpl
    vol = floor_to_step(min(raw, spec.volume_max), spec.volume_step)
    if vol < spec.volume_min - 1e-12:
        res.reason = (f"position size is below the broker minimum: {spec.volume_min} lots would risk "
                      f"{spec.volume_min * lpl:.2f}, the limit is {target:.2f}")
        return res
    res.ok, res.volume = True, round(vol, 8)
    res.risk_money = round(vol * lpl, 2)
    return res


def remaining_risk(side: int, volume: float, entry: float, sl: float | None, spec: SymbolSpec,
                   slippage_pips: float = 0.0, unprotected: float | None = None) -> float:
    """Money still at risk on an open position or pending order, from the CURRENT stop.
    The loss counts only while the stop is on the losing side of the entry. A stop at breakeven or in profit leaves
    the cost of getting out (spread allowance, slippage, commission), which is never counted as zero.
    No stop at all means `unprotected` (a configured amount), or infinite."""
    if sl is None:
        return unprotected if unprotected is not None else float("inf")
    adverse = max(side * (entry - sl), 0.0)  # price distance lost if the stop fills, 0 once the stop is at or past entry
    ticks = (adverse + slippage_pips * spec.pip_size) / spec.tick_size
    return volume * (ticks * spec.tick_value + spec.commission_per_lot)
