"""Plain data objects shared by every broker and by the trading engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd


@dataclass
class AccountInfo:
    broker: str
    account_id: str
    currency: str
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    margin_level: float | None  # percent, None when no margin is used
    is_live: bool
    unrealized_pnl: float = 0.0
    time: pd.Timestamp | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = pd.Timestamp(self.time).isoformat() if self.time is not None else None
        return d


@dataclass
class SymbolSpec:
    symbol: str
    base_ccy: str
    quote_ccy: str
    digits: int
    pip_size: float
    tick_size: float
    contract_size: float          # units of base currency per 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    tick_value: float             # account-currency LOSS of one tick for one lot
    commission_per_lot: float = 0.0  # round turn, account currency
    min_stop_distance: float = 0.0   # price distance the broker needs between price and a stop

    def pip_value_per_lot(self) -> float:
        return self.tick_value * self.pip_size / self.tick_size


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    time: pd.Timestamp

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2


@dataclass
class Position:
    position_id: str
    symbol: str
    side: int          # +1 long, -1 short
    volume: float
    open_price: float
    sl: float | None
    tp: float | None
    open_time: pd.Timestamp
    unrealized_pnl: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    comment: str = ""  # carries our client order id so a position can be matched to the order that created it


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    side: int
    volume: float
    price: float
    sl: float | None
    tp: float | None
    comment: str = ""
    kind: str = "limit"


@dataclass
class OrderRequest:
    client_id: str
    symbol: str
    side: int
    volume: float
    kind: str = "market"   # market | limit
    price: float | None = None
    sl: float | None = None
    tp: float | None = None


@dataclass
class OrderResult:
    ok: bool
    status: str            # filled | accepted | rejected
    order_id: str = ""
    position_id: str = ""
    fill_price: float | None = None
    filled_volume: float = 0.0
    message: str = ""


@dataclass
class ClosedInfo:
    position_id: str
    close_price: float
    pnl: float             # net of commission and swap
    close_time: pd.Timestamp
    reason: str            # sl | tp | manual | unknown


class BrokerError(Exception):
    """The broker said no, or something is wrong with the request."""


class BrokerConnectionError(BrokerError):
    """Not connected / network down. Nothing was sent, or the result is unknown."""


class BrokerTimeout(BrokerError):
    """The request may or may not have been executed. NEVER retry blindly: reconcile first."""
