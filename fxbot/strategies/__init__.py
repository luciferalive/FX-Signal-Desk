"""Strategy registry. Add a strategy by writing a class (see base.Strategy) and listing it here.

Families group strategies that measure the same idea, so the confluence engine never counts two trend
indicators as two independent confirmations."""
from __future__ import annotations

from .base import Signal, Strategy
from .breakout import BollingerSqueeze, BreakoutRetest, Donchian, NR7
from .ict import ICT
from .mean_reversion import BollingerReversion, RSI2
from .price_action import FailedBreakout, SetForget
from .scalping import SessionBreakout
from .swing import HeikinStoch
from .trend import EmaCross, TrendPull

STRATEGIES = {c.name: c for c in (
    ICT, TrendPull, EmaCross, Donchian, BreakoutRetest, BollingerSqueeze, NR7,
    SetForget, FailedBreakout, RSI2, BollingerReversion, SessionBreakout, HeikinStoch)}

FAMILIES = {
    "ict": "ICT / SMC",
    "trend": "Trend following",
    "price_action": "Price action",
    "breakout": "Breakout",
    "mean_reversion": "Mean reversion",
    "scalping": "Scalping",
    "swing": "Swing trading",
}

__all__ = ["Signal", "Strategy", "STRATEGIES", "FAMILIES"]
