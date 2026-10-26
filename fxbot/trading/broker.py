"""The broker interface every integration implements. The strategy engine only ever sees this."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import (AccountInfo, BrokerError, ClosedInfo, OrderRequest, OrderResult, PendingOrder, Position,
                     Quote, SymbolSpec)


class Broker(ABC):
    name = "broker"

    # ---- connection
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    # ---- account and market
    @abstractmethod
    def account(self) -> AccountInfo: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def pending_orders(self) -> list[PendingOrder]: ...

    @abstractmethod
    def symbol_spec(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    def quote(self, symbol: str) -> Quote: ...

    # ---- trading
    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def modify_position(self, position_id: str, sl: float | None = None, tp: float | None = None) -> None:
        """Change SL and/or TP. Must never leave the position without the stop it already had if the call fails."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    def close_position(self, position_id: str, volume: float | None = None) -> OrderResult:
        """volume None = full close, otherwise a partial close that keeps the position id."""

    # ---- reconciliation
    @abstractmethod
    def find_by_client_id(self, client_id: str):
        """('position', Position) | ('order', PendingOrder) | None. Used after a timeout to learn what really happened."""

    @abstractmethod
    def closed_info(self, position_id: str) -> ClosedInfo | None: ...

    def margin_required(self, symbol: str, volume: float, price: float) -> float:
        """Margin (account currency) the broker would hold for this volume. Brokers that cannot say raise, and the
        risk manager then rejects the trade rather than guess."""
        raise BrokerError(f"{self.name}: margin requirement is not available")

    def convert(self, amount: float, frm: str, to: str) -> float:
        raise BrokerError(f"{self.name}: currency conversion {frm}->{to} is not available")
