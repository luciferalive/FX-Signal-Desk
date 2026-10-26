"""MetaTrader 5 adapter, used for XM (and any other MT5 broker).

HOW IT CONNECTS: XM has no web trading API of its own. Retail XM accounts trade through MetaTrader. The official
`MetaTrader5` Python package (pip install MetaTrader5) talks to a MetaTrader 5 TERMINAL that is installed, running and
logged in to your XM account on the same computer (in practice a Windows PC). So the bot must run on that computer,
not on GitHub. XM MT4 accounts have no official Python interface and are not supported here: you need an XM **MT5** account.

TESTED: only against a fake MT5 module that follows the documented function names (account_info, symbol_info,
symbol_info_tick, positions_get, orders_get, order_send, order_calc_margin, history_deals_get). It has NOT been run
against a real XM terminal, because this build environment cannot run MetaTrader. Run it on an XM DEMO account first.

SECURITY: by default nothing is passed to the terminal: it uses the account you are already logged in with, so the bot
never sees your password. Optional MT5_LOGIN / MT5_PASSWORD / MT5_SERVER environment variables exist for unattended
use; they are never printed or logged.
"""
from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta

import pandas as pd

from ..backtest import pip_size
from .broker import Broker
from .models import (AccountInfo, BrokerConnectionError, BrokerError, BrokerTimeout, ClosedInfo, OrderRequest,
                     OrderResult, PendingOrder, Position, Quote, SymbolSpec)


def _get(obj, name, default=0):
    return getattr(obj, name, default)


class MT5Broker(Broker):
    name = "mt5"

    def __init__(self, mt5=None, suffix: str = "", symbol_map: dict | None = None,
                 server_utc_offset_hours: float | None = None, commission_per_lot_round_turn: float = 0.0,
                 deviation_points: int = 20, magic: int = 724001, now_fn=None):
        self._mt5 = mt5
        self.suffix = suffix or ""
        self.symbol_map = dict(symbol_map or {})
        self._off = None if server_utc_offset_hours is None else int(round(server_utc_offset_hours * 3600))
        self._fixed_off = server_utc_offset_hours is not None
        self.commission = float(commission_per_lot_round_turn)
        self.deviation, self.magic = int(deviation_points), int(magic)
        self._now = now_fn or _time.time
        self._connected = False
        self.is_live = False
        self.algo_allowed = True
        self.login = ""
        self.server = ""

    def __repr__(self) -> str:   # never show credentials
        return f"MT5Broker(login={self.login}, server={self.server})"

    # ---------------------------------------------------------------- module and names
    @property
    def m(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5    # noqa: N813
            except ImportError as e:
                raise BrokerConnectionError(
                    "the MetaTrader5 Python package is not installed (pip install MetaTrader5). It needs the MetaTrader 5 "
                    "terminal installed and running on this computer.") from e
            self._mt5 = mt5
        return self._mt5

    def _sym(self, canonical: str) -> str:
        return self.symbol_map.get(canonical, canonical + self.suffix)

    def _canon(self, broker_symbol: str) -> str:
        for k, v in self.symbol_map.items():
            if v == broker_symbol:
                return k
        if self.suffix and broker_symbol.endswith(self.suffix):
            return broker_symbol[: -len(self.suffix)]
        return broker_symbol

    def _err(self) -> str:
        try:
            return str(self.m.last_error())
        except Exception:
            return "unknown"

    # ---------------------------------------------------------------- connection
    def connect(self) -> None:
        m = self.m
        login, pw, server = os.getenv("MT5_LOGIN"), os.getenv("MT5_PASSWORD"), os.getenv("MT5_SERVER")
        ok = m.initialize(login=int(login), password=pw, server=server) if (login and pw and server) else m.initialize()
        if not ok:
            raise BrokerConnectionError(f"could not attach to the MetaTrader 5 terminal ({self._err()}). Is it open and logged in?")
        ai = m.account_info()
        if ai is None:
            raise BrokerConnectionError("the terminal is not logged in to an account")
        self.is_live = _get(ai, "trade_mode") == getattr(m, "ACCOUNT_TRADE_MODE_REAL", 2)
        self.login, self.server = str(_get(ai, "login", "")), str(_get(ai, "server", ""))
        ti = m.terminal_info()
        self.algo_allowed = bool(ti is None or _get(ti, "trade_allowed", True))
        self._connected = True

    def disconnect(self) -> None:
        try:
            self.m.shutdown()
        finally:
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _need(self) -> None:
        if not self._connected:
            raise BrokerConnectionError("not connected")

    # ---------------------------------------------------------------- time
    def _to_utc(self, epoch: float, fresh: bool = False) -> pd.Timestamp:
        """MT5 times are in the broker's SERVER time (XM: about UTC+2 / +3). Convert to real UTC. The offset comes from
        the config, or is detected from a fresh tick and rounded to a quarter hour."""
        if not self._fixed_off and (self._off is None or fresh):
            raw = epoch - self._now()
            off = int(round(raw / 900.0) * 900)
            if abs(raw - off) <= 120 and abs(off) <= 4 * 3600:
                self._off = off
            elif self._off is None:
                raise BrokerError("cannot work out the broker's server time offset (market closed or stale data). "
                                  "Set \"server_utc_offset_hours\" in trading.json.")
        return pd.Timestamp(epoch - (self._off or 0), unit="s", tz="UTC")

    # ---------------------------------------------------------------- account
    def account(self) -> AccountInfo:
        self._need()
        ai = self.m.account_info()
        if ai is None:
            raise BrokerConnectionError("account info unavailable: " + self._err())
        used = float(_get(ai, "margin"))
        return AccountInfo(self.name, str(_get(ai, "login")), str(_get(ai, "currency")), float(_get(ai, "balance")),
                           float(_get(ai, "equity")), used, float(_get(ai, "margin_free")),
                           float(_get(ai, "margin_level")) if used > 0 else None, self.is_live, float(_get(ai, "profit")),
                           pd.Timestamp.now(tz="UTC"))

    def _position(self, p) -> Position:
        m = self.m
        side = 1 if _get(p, "type") == getattr(m, "POSITION_TYPE_BUY", 0) else -1
        return Position(str(_get(p, "ticket")), self._canon(_get(p, "symbol", "")), side, float(_get(p, "volume")),
                        float(_get(p, "price_open")), float(p.sl) if _get(p, "sl") else None,
                        float(p.tp) if _get(p, "tp") else None, self._to_utc(float(_get(p, "time"))),
                        float(_get(p, "profit")), float(_get(p, "swap")), 0.0, str(_get(p, "comment", "")))

    def positions(self) -> list[Position]:
        self._need()
        res = self.m.positions_get()
        if res is None:
            if self._err().startswith("(1,") or "Success" in self._err():
                return []
            raise BrokerConnectionError("positions unavailable: " + self._err())
        return [self._position(p) for p in res]

    def pending_orders(self) -> list[PendingOrder]:
        self._need()
        m = self.m
        res = m.orders_get() or []
        kinds = {getattr(m, "ORDER_TYPE_BUY_LIMIT", 2): (1, "limit"), getattr(m, "ORDER_TYPE_SELL_LIMIT", 3): (-1, "limit"),
                 getattr(m, "ORDER_TYPE_BUY_STOP", 4): (1, "stop"), getattr(m, "ORDER_TYPE_SELL_STOP", 5): (-1, "stop")}
        out = []
        for o in res:
            if _get(o, "type") not in kinds:
                continue
            side, kind = kinds[o.type]
            out.append(PendingOrder(str(o.ticket), self._canon(o.symbol), side, float(o.volume_current), float(o.price_open),
                                    float(o.sl) if _get(o, "sl") else None, float(o.tp) if _get(o, "tp") else None,
                                    str(_get(o, "comment", "")), kind))
        return out

    # ---------------------------------------------------------------- instruments and prices
    def _info(self, symbol: str):
        m = self.m
        info = m.symbol_info(self._sym(symbol))
        if info is None:
            raise BrokerError(f"{self._sym(symbol)} is not available on this account (check the symbol suffix in trading.json "
                              "against the names in MetaTrader's Market Watch)")
        if not _get(info, "visible", True):
            m.symbol_select(self._sym(symbol), True)
        return info

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._need()
        i = self._info(symbol)
        point = float(i.point)
        tick = float(_get(i, "trade_tick_size", point)) or point
        loss = float(_get(i, "trade_tick_value_loss", 0.0)) or float(_get(i, "trade_tick_value", 0.0))
        if loss <= 0:
            raise BrokerError(f"MT5 gave no tick value for {symbol}; cannot value a pip safely")
        return SymbolSpec(symbol, str(_get(i, "currency_base", symbol[:3])), str(_get(i, "currency_profit", symbol[3:6])),
                          int(i.digits), pip_size(symbol), tick, float(i.trade_contract_size), float(i.volume_min),
                          float(i.volume_max), float(i.volume_step), loss, self.commission,
                          max(float(_get(i, "trade_stops_level", 0)), float(_get(i, "trade_freeze_level", 0))) * point)

    def quote(self, symbol: str) -> Quote:
        self._need()
        t = self.m.symbol_info_tick(self._sym(symbol))
        if t is None:
            raise BrokerError(f"no tick for {symbol}: {self._err()}")
        return Quote(symbol, float(t.bid), float(t.ask), self._to_utc(float(t.time), fresh=True))

    def margin_required(self, symbol: str, volume: float, price: float) -> float:
        self._need()
        v = self.m.order_calc_margin(getattr(self.m, "ORDER_TYPE_BUY", 0), self._sym(symbol), volume, price)
        if v is None:
            raise BrokerError("MT5 could not calculate the margin: " + self._err())
        return float(v)

    # ---------------------------------------------------------------- trading
    def _filling(self, symbol: str):
        m = self.m
        mode = int(_get(self._info(symbol), "filling_mode", 0))
        if mode & 2:
            return getattr(m, "ORDER_FILLING_IOC", 1)
        if mode & 1:
            return getattr(m, "ORDER_FILLING_FOK", 0)
        return getattr(m, "ORDER_FILLING_RETURN", 2)

    def _send(self, request: dict):
        res = self.m.order_send(request)
        if res is None:
            raise BrokerTimeout(f"order_send returned nothing ({self._err()}): the result is unknown")
        return res

    def place_order(self, req: OrderRequest) -> OrderResult:
        self._need()
        m = self.m
        if not self.algo_allowed:
            raise BrokerError("automated trading is switched off in the MetaTrader terminal (turn on the 'Algo Trading' button)")
        dup = self.find_by_client_id(req.client_id)
        if dup and dup[0] == "position":     # idempotent: the same client id never opens a second position
            p = dup[1]
            return OrderResult(True, "filled", p.position_id, p.position_id, p.open_price, p.volume, "duplicate client id")
        sym = self._sym(req.symbol)
        info = self._info(req.symbol)
        tick = m.symbol_info_tick(sym)
        if tick is None:
            raise BrokerError(f"no tick for {req.symbol}")
        buy = req.side > 0
        r = {"symbol": sym, "volume": float(req.volume), "magic": self.magic, "comment": req.client_id[:31],
             "type_time": getattr(m, "ORDER_TIME_GTC", 0), "sl": float(req.sl or 0.0), "tp": float(req.tp or 0.0)}
        if req.kind == "market":
            r.update(action=getattr(m, "TRADE_ACTION_DEAL", 1), type=getattr(m, "ORDER_TYPE_BUY", 0) if buy else getattr(m, "ORDER_TYPE_SELL", 1),
                     price=float(tick.ask if buy else tick.bid), deviation=self.deviation, type_filling=self._filling(req.symbol))
        else:
            r.update(action=getattr(m, "TRADE_ACTION_PENDING", 5),
                     type=getattr(m, "ORDER_TYPE_BUY_LIMIT", 2) if buy else getattr(m, "ORDER_TYPE_SELL_LIMIT", 3),
                     price=float(req.price), type_filling=getattr(m, "ORDER_FILLING_RETURN", 2))
        res = self._send(r)
        code = int(res.retcode)
        done, partial, placed = (getattr(m, "TRADE_RETCODE_DONE", 10009), getattr(m, "TRADE_RETCODE_DONE_PARTIAL", 10010),
                                 getattr(m, "TRADE_RETCODE_PLACED", 10008))
        if code in (getattr(m, "TRADE_RETCODE_TIMEOUT", 10012), getattr(m, "TRADE_RETCODE_CONNECTION", 10031)):
            raise BrokerTimeout(f"MT5 retcode {code} ({_get(res, 'comment', '')}): the result is unknown")
        if code in (done, partial) and req.kind == "market":
            found = self.find_by_client_id(req.client_id)   # confirm from the terminal's positions, not just the reply
            if found and found[0] == "position":
                p = found[1]
                return OrderResult(True, "filled", str(_get(res, "order", p.position_id)), p.position_id, p.open_price, p.volume)
            return OrderResult(True, "filled", str(_get(res, "order", "")), str(_get(res, "order", "")),
                               float(_get(res, "price", 0.0)) or None, float(_get(res, "volume", req.volume)))
        if code == placed:
            return OrderResult(True, "accepted", str(_get(res, "order", "")))
        return OrderResult(False, "rejected", message=f"MT5 retcode {code}: {_get(res, 'comment', '')}")

    def _raw_position(self, position_id: str):
        res = self.m.positions_get(ticket=int(position_id))
        if not res:
            raise BrokerError(f"position {position_id} not found")
        return res[0]

    def modify_position(self, position_id: str, sl: float | None = None, tp: float | None = None) -> None:
        self._need()
        m = self.m
        p = self._raw_position(position_id)
        r = {"action": getattr(m, "TRADE_ACTION_SLTP", 6), "symbol": p.symbol, "position": int(position_id), "magic": self.magic,
             "sl": float(sl) if sl is not None else float(_get(p, "sl", 0.0)),      # SLTP needs BOTH values: keep the other
             "tp": float(tp) if tp is not None else float(_get(p, "tp", 0.0))}
        res = m.order_send(r)
        if res is None or int(res.retcode) != getattr(m, "TRADE_RETCODE_DONE", 10009):
            code = None if res is None else int(res.retcode)
            raise BrokerError(f"MT5 refused the stop/target change (retcode {code}): {_get(res, 'comment', self._err()) if res else self._err()}")

    def cancel_order(self, order_id: str) -> None:
        self._need()
        m = self.m
        res = m.order_send({"action": getattr(m, "TRADE_ACTION_REMOVE", 8), "order": int(order_id)})
        if res is None or int(res.retcode) != getattr(m, "TRADE_RETCODE_DONE", 10009):
            raise BrokerError("MT5 could not cancel the order")

    def close_position(self, position_id: str, volume: float | None = None) -> OrderResult:
        self._need()
        m = self.m
        p = self._raw_position(position_id)
        buy_pos = p.type == getattr(m, "POSITION_TYPE_BUY", 0)
        tick = m.symbol_info_tick(p.symbol)
        if tick is None:
            raise BrokerError(f"no tick for {p.symbol}")
        vol = float(p.volume) if volume is None else float(volume)
        r = {"action": getattr(m, "TRADE_ACTION_DEAL", 1), "symbol": p.symbol, "position": int(position_id), "volume": vol,
             "type": getattr(m, "ORDER_TYPE_SELL", 1) if buy_pos else getattr(m, "ORDER_TYPE_BUY", 0),
             "price": float(tick.bid if buy_pos else tick.ask), "deviation": self.deviation, "magic": self.magic,
             "comment": "fxbot close", "type_time": getattr(m, "ORDER_TIME_GTC", 0),
             "type_filling": self._filling(self._canon(p.symbol))}
        res = self._send(r)
        code = int(res.retcode)
        if code in (getattr(m, "TRADE_RETCODE_DONE", 10009), getattr(m, "TRADE_RETCODE_DONE_PARTIAL", 10010)):
            return OrderResult(True, "filled", str(_get(res, "order", "")), position_id, float(_get(res, "price", 0.0)) or None,
                               float(_get(res, "volume", vol)))
        return OrderResult(False, "rejected", message=f"MT5 retcode {code}: {_get(res, 'comment', '')}")

    # ---------------------------------------------------------------- reconciliation
    def find_by_client_id(self, client_id: str):
        self._need()
        tag = client_id[:31]
        for p in (self.m.positions_get() or []):
            if str(_get(p, "comment", "")) == tag:
                return ("position", self._position(p))
        for o in self.pending_orders():
            if o.comment == tag:
                return ("order", o)
        now = datetime.utcnow()
        deals = self.m.history_deals_get(now - timedelta(days=8), now + timedelta(days=2)) or []
        for d in deals:
            if str(_get(d, "comment", "")) == tag:
                ci = self.closed_info(str(_get(d, "position_id")))
                if ci:
                    return ("closed", ci)
        return None

    def closed_info(self, position_id: str) -> ClosedInfo | None:
        self._need()
        m = self.m
        deals = list(self.m.history_deals_get(position=int(position_id)) or [])
        out = [d for d in deals if _get(d, "entry") in (getattr(m, "DEAL_ENTRY_OUT", 1), getattr(m, "DEAL_ENTRY_OUT_BY", 3))]
        if not out:
            return None
        last = max(out, key=lambda d: (_get(d, "time"), _get(d, "ticket", 0)))
        take = deals if len(out) == 1 else [last]    # with partial closes the earlier ones were already counted
        pnl = sum(float(_get(d, "profit")) + float(_get(d, "commission")) + float(_get(d, "swap")) + float(_get(d, "fee", 0.0))
                  for d in take)
        reason = _get(last, "reason")
        why = "sl" if reason == getattr(m, "DEAL_REASON_SL", 4) else "tp" if reason == getattr(m, "DEAL_REASON_TP", 5) else "manual"
        return ClosedInfo(position_id, float(last.price), round(pnl, 2), self._to_utc(float(last.time)), why)
