"""SimBroker: a complete simulated broker account for DEMO use and for tests. No network, no credentials, no real money.

It behaves like a strict broker: volume limits and steps, stop distance, margin, commission, slippage, SL/TP fills when
quotes move, and it can be told to fail in the ways real brokers fail (timeouts, rejects, partial fills, lost
connection, stop-modify errors) so the engine's recovery code can be tested for real."""
from __future__ import annotations

import copy
import itertools

import pandas as pd

from .broker import Broker
from .models import (AccountInfo, BrokerConnectionError, BrokerError, BrokerTimeout, ClosedInfo, OrderRequest,
                     OrderResult, PendingOrder, Position, Quote, SymbolSpec)
from .sizing import convert_ccy, floor_to_step, is_valid_volume, tick_value_from_rates

# Plausible USD rates, only used to convert profit and loss in the simulation. Replace with set_rates() in tests.
DEFAULT_RATES = {"EURUSD": 1.10, "GBPUSD": 1.30, "AUDUSD": 0.66, "NZDUSD": 0.60, "USDJPY": 150.0, "USDCAD": 1.36,
                 "USDCHF": 0.88, "XAUUSD": 2650.0}


def _split(symbol: str) -> tuple[str, str]:
    return symbol[:3], symbol[3:6]


class SimBroker(Broker):
    name = "sim"

    def __init__(self, balance: float = 10_000.0, currency: str = "USD", leverage: float = 30.0, is_live: bool = False,
                 commission_per_lot: float = 0.0, slippage_pips: float = 0.0, rates: dict | None = None,
                 account_id: str = "SIM-0001"):
        self.currency, self.leverage, self.is_live = currency, leverage, is_live
        self.commission_per_lot, self.slippage_pips = commission_per_lot, slippage_pips
        self.account_id = account_id
        self.balance = balance
        self.rates = dict(rates or DEFAULT_RATES)
        self._quotes: dict[str, Quote] = {}
        self._positions: dict[str, Position] = {}
        self._pending: dict[str, PendingOrder] = {}
        self._closed: dict[str, ClosedInfo] = {}
        self._by_client: dict[str, tuple[str, str]] = {}   # client_id -> ("position"|"order", id)
        self._ids = itertools.count(1)
        self._connected = False
        self._faults: dict[str, int] = {}
        self.log: list[tuple] = []         # every request the broker received, for tests
        self.specs: dict[str, SymbolSpec] = {}

    # ---------------------------------------------------------------- test controls
    def set_rates(self, rates: dict) -> None:
        self.rates.update(rates)

    def fail_next(self, kind: str, times: int = 1) -> None:
        """kind: connection | timeout_before | timeout_after | reject | partial | modify_fail | close_fail | modify_drop_stop"""
        self._faults[kind] = self._faults.get(kind, 0) + times

    def _fault(self, kind: str) -> bool:
        if self._faults.get(kind, 0) > 0:
            self._faults[kind] -= 1
            return True
        return False

    def snapshot(self) -> dict:
        """The broker's own state, to simulate 'the broker kept running while the application restarted'."""
        return copy.deepcopy(dict(balance=self.balance, positions=self._positions, pending=self._pending,
                                  closed=self._closed, by_client=self._by_client, quotes=self._quotes))

    # ---------------------------------------------------------------- connection
    def connect(self) -> None:
        if self._fault("connection"):
            raise BrokerConnectionError("simulated connection failure")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _need(self) -> None:
        if not self._connected or self._fault("connection"):
            raise BrokerConnectionError("not connected")

    # ---------------------------------------------------------------- market
    def symbol_spec(self, symbol: str) -> SymbolSpec:
        if symbol in self.specs:
            return self.specs[symbol]
        base, quote = _split(symbol)
        gold = symbol.startswith("XAU")
        jpy = quote == "JPY"
        contract = 100.0 if gold else 100_000.0
        pip = 0.1 if gold else (0.01 if jpy else 0.0001)
        tick = pip / 10 if not gold else 0.01
        tv = tick_value_from_rates(contract, tick, quote, self.currency, self.rates)
        return SymbolSpec(symbol, base, quote, digits=3 if jpy else (2 if gold else 5), pip_size=pip, tick_size=tick,
                          contract_size=contract, volume_min=0.01, volume_max=50.0, volume_step=0.01, tick_value=tv,
                          commission_per_lot=self.commission_per_lot, min_stop_distance=pip * 2)

    def set_quote(self, symbol: str, bid: float, ask: float, time) -> None:
        self._quotes[symbol] = Quote(symbol, float(bid), float(ask), pd.Timestamp(time))
        self._update_rate(symbol, (bid + ask) / 2)
        self._process_stops(symbol)
        self._process_pending(symbol)

    def _update_rate(self, symbol: str, mid: float) -> None:
        if len(symbol) == 6 and symbol[:3].isalpha():
            self.rates[symbol] = mid

    def quote(self, symbol: str) -> Quote:
        self._need()
        if symbol not in self._quotes:
            raise BrokerError(f"no quote for {symbol}")
        return self._quotes[symbol]

    # ---------------------------------------------------------------- pnl
    def _pnl(self, p: Position, price: float, volume: float) -> float:
        spec = self.symbol_spec(p.symbol)
        quote_amount = p.side * (price - p.open_price) * volume * spec.contract_size
        return convert_ccy(quote_amount, spec.quote_ccy, self.currency, self.rates)

    def _unrealized(self, p: Position) -> float:
        q = self._quotes.get(p.symbol)
        if not q:
            return 0.0
        return self._pnl(p, q.bid if p.side > 0 else q.ask, p.volume)

    def _margin_used(self) -> float:
        total = 0.0
        for p in self._positions.values():
            total += self.margin_required(p.symbol, p.volume, p.open_price)
        return total

    def margin_required(self, symbol: str, volume: float, price: float) -> float:
        spec = self.symbol_spec(symbol)
        notional_base = volume * spec.contract_size
        in_acc = convert_ccy(notional_base, spec.base_ccy, self.currency, self.rates) if not symbol.startswith("XAU") \
            else convert_ccy(notional_base * price, "USD", self.currency, self.rates)
        return in_acc / self.leverage

    def account(self) -> AccountInfo:
        self._need()
        unreal = sum(self._unrealized(p) for p in self._positions.values())
        equity = self.balance + unreal
        used = self._margin_used()
        for p in self._positions.values():
            p.unrealized_pnl = self._unrealized(p)
        return AccountInfo(self.name, self.account_id, self.currency, round(self.balance, 2), round(equity, 2),
                           round(used, 2), round(equity - used, 2), (equity / used * 100) if used else None,
                           self.is_live, round(unreal, 2),
                           max((q.time for q in self._quotes.values()), default=None))

    def positions(self) -> list[Position]:
        self._need()
        for p in self._positions.values():
            p.unrealized_pnl = self._unrealized(p)
        return [copy.copy(p) for p in self._positions.values()]

    def pending_orders(self) -> list[PendingOrder]:
        self._need()
        return [copy.copy(o) for o in self._pending.values()]

    # ---------------------------------------------------------------- trading
    def _validate(self, req: OrderRequest, spec: SymbolSpec, price: float) -> str | None:
        if not is_valid_volume(req.volume, spec):
            return f"invalid volume {req.volume} (min {spec.volume_min}, step {spec.volume_step}, max {spec.volume_max})"
        for name, lvl in (("stop loss", req.sl),):
            if lvl is not None and req.side * (price - lvl) < spec.min_stop_distance:
                return f"{name} too close to the price or on the wrong side"
        if req.tp is not None and req.side * (req.tp - price) < spec.min_stop_distance:
            return "take profit too close to the price or on the wrong side"
        free = self.account().margin_free
        need = self.margin_required(req.symbol, req.volume, price)
        if need > free:
            return f"insufficient margin: needs {need:.2f}, free {free:.2f}"
        return None

    def place_order(self, req: OrderRequest) -> OrderResult:
        self._need()
        self.log.append(("place", req.client_id, req.symbol, req.side, req.volume, req.sl, req.tp))
        if req.client_id in self._by_client:   # idempotent: the same client id never creates a second order
            kind, oid = self._by_client[req.client_id]
            pos = self._positions.get(oid)
            return OrderResult(True, "filled" if pos else "accepted", order_id=oid, position_id=oid if pos else "",
                               fill_price=pos.open_price if pos else None, filled_volume=pos.volume if pos else 0.0,
                               message="duplicate client id, existing order returned")
        if self._fault("timeout_before"):
            raise BrokerTimeout("simulated timeout, the order was NOT received")
        if self._fault("reject"):
            return OrderResult(False, "rejected", message="simulated broker rejection")
        q = self.quote(req.symbol)
        spec = self.symbol_spec(req.symbol)
        slip = self.slippage_pips * spec.pip_size
        fill = (q.ask + slip) if req.side > 0 else (q.bid - slip)
        if req.kind == "market":
            err = self._validate(req, spec, fill)
            if err:
                return OrderResult(False, "rejected", message=err)
            vol = req.volume
            if self._fault("partial"):
                vol = max(floor_to_step(req.volume / 2, spec.volume_step), spec.volume_min)
            pid = f"P{next(self._ids)}"
            self._positions[pid] = Position(pid, req.symbol, req.side, vol, fill, req.sl, req.tp, q.time, comment=req.client_id)
            self.balance -= self.commission_per_lot * vol / 2   # half the round-turn commission at entry
            self._by_client[req.client_id] = ("position", pid)
            res = OrderResult(True, "filled", order_id=pid, position_id=pid, fill_price=fill, filled_volume=vol)
        else:
            oid = f"O{next(self._ids)}"
            self._pending[oid] = PendingOrder(oid, req.symbol, req.side, req.volume, float(req.price), req.sl, req.tp,
                                              comment=req.client_id, kind=req.kind)
            self._by_client[req.client_id] = ("order", oid)
            res = OrderResult(True, "accepted", order_id=oid)
        if self._fault("timeout_after"):
            raise BrokerTimeout("simulated timeout, the order WAS executed but the answer was lost")
        return res

    def modify_position(self, position_id: str, sl: float | None = None, tp: float | None = None) -> None:
        self._need()
        self.log.append(("modify", position_id, sl, tp))
        p = self._positions.get(position_id)
        if p is None:
            raise BrokerError(f"position {position_id} not found")
        if self._fault("modify_drop_stop"):   # a nasty broker bug: the old stop disappears and the new one is refused
            p.sl = None
            raise BrokerError("simulated: stop removed and modification refused")
        if self._fault("modify_fail"):
            raise BrokerError("simulated stop modification failure")
        spec = self.symbol_spec(p.symbol)
        q = self.quote(p.symbol)
        price = q.bid if p.side > 0 else q.ask
        if sl is not None and p.side * (price - sl) < spec.min_stop_distance:
            raise BrokerError("new stop is too close to the price or on the wrong side")
        if sl is not None:
            p.sl = sl
        if tp is not None:
            p.tp = tp

    def cancel_order(self, order_id: str) -> None:
        self._need()
        self.log.append(("cancel", order_id))
        if order_id not in self._pending:
            raise BrokerError(f"order {order_id} not found")
        del self._pending[order_id]

    def close_position(self, position_id: str, volume: float | None = None) -> OrderResult:
        self._need()
        self.log.append(("close", position_id, volume))
        p = self._positions.get(position_id)
        if p is None:
            raise BrokerError(f"position {position_id} not found")
        if self._fault("close_fail"):
            return OrderResult(False, "rejected", message="simulated close failure")
        spec = self.symbol_spec(p.symbol)
        vol = p.volume if volume is None else float(volume)
        if vol > p.volume + 1e-12:
            return OrderResult(False, "rejected", message="close volume is larger than the position")
        if vol < p.volume - 1e-12:
            rest = p.volume - vol
            if not is_valid_volume(vol, spec) or rest < spec.volume_min - 1e-12:
                return OrderResult(False, "rejected", message=f"invalid partial close {vol} of {p.volume}")
        q = self.quote(p.symbol)
        slip = self.slippage_pips * spec.pip_size
        price = (q.bid - slip) if p.side > 0 else (q.ask + slip)
        return self._do_close(p, vol, price, "manual")

    def _do_close(self, p: Position, vol: float, price: float, reason: str) -> OrderResult:
        pnl = self._pnl(p, price, vol) - self.commission_per_lot * vol / 2
        self.balance += pnl
        info_pnl = pnl
        if vol >= p.volume - 1e-12:
            self._closed[p.position_id] = ClosedInfo(p.position_id, price, round(info_pnl, 2), self._now(p.symbol), reason)
            del self._positions[p.position_id]
        else:
            p.volume = round(p.volume - vol, 8)
        return OrderResult(True, "filled", order_id=p.position_id, position_id=p.position_id, fill_price=price,
                           filled_volume=vol, message=f"pnl {pnl:.2f}")

    def _now(self, symbol: str):
        q = self._quotes.get(symbol)
        return q.time if q else pd.Timestamp.now(tz="UTC")

    # ---------------------------------------------------------------- price movement
    def _process_stops(self, symbol: str) -> None:
        q = self._quotes[symbol]
        for p in list(self._positions.values()):
            if p.symbol != symbol:
                continue
            exit_px = q.bid if p.side > 0 else q.ask
            if p.sl is not None and p.side * (exit_px - p.sl) <= 0:      # stop first when both are inside one move
                self._do_close(p, p.volume, min(exit_px, p.sl) if p.side > 0 else max(exit_px, p.sl), "sl")
            elif p.tp is not None and p.side * (exit_px - p.tp) >= 0:
                self._do_close(p, p.volume, p.tp, "tp")

    def _process_pending(self, symbol: str) -> None:
        q = self._quotes[symbol]
        for o in list(self._pending.values()):
            if o.symbol != symbol:
                continue
            hit = (o.side > 0 and q.ask <= o.price) or (o.side < 0 and q.bid >= o.price)
            if hit:
                del self._pending[o.order_id]
                pid = f"P{next(self._ids)}"
                self._positions[pid] = Position(pid, symbol, o.side, o.volume, o.price, o.sl, o.tp, q.time, comment=o.comment)
                self._by_client[o.comment] = ("position", pid)

    # ---------------------------------------------------------------- reconciliation
    def find_by_client_id(self, client_id: str):
        self._need()
        hit = self._by_client.get(client_id)
        if not hit:
            return None
        kind, oid = hit
        if kind == "position" and oid in self._positions:
            return ("position", copy.copy(self._positions[oid]))
        if kind == "order" and oid in self._pending:
            return ("order", copy.copy(self._pending[oid]))
        if oid in self._closed:
            return ("closed", self._closed[oid])
        return None

    def closed_info(self, position_id: str) -> ClosedInfo | None:
        self._need()
        return self._closed.get(position_id)

    def convert(self, amount: float, frm: str, to: str) -> float:
        return convert_ccy(amount, frm, to, self.rates)
