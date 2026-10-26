"""OANDA v20 REST adapter (practice = demo account, live = real account).

Written against OANDA's documented v20 REST API. It has been tested here ONLY against recorded-style mock responses,
never against a real OANDA account, because this build environment cannot reach OANDA. Treat the first run on a
PRACTICE account as the real test. Credentials come from environment variables and are never logged:

    OANDA_API_TOKEN   personal access token (OANDA hub > Manage API access)
    OANDA_ACCOUNT_ID  e.g. 101-004-1234567-001
    OANDA_ENV         practice (default) or live

Mapping: our "position" = an OANDA trade (so partial closes and stops work per trade). Volume is in lots of
100,000 units; OANDA works in units, so 0.01 lot = 1,000 units. Orders are market FOK with the stop attached on fill.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

import pandas as pd

from .broker import Broker
from .models import (AccountInfo, BrokerConnectionError, BrokerError, BrokerTimeout, ClosedInfo, OrderRequest,
                     OrderResult, PendingOrder, Position, Quote, SymbolSpec)

LOT_UNITS = 100_000
HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}


def to_oanda(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("_", "")
    return f"{s[:3]}_{s[3:]}"


def from_oanda(instrument: str) -> str:
    return instrument.replace("_", "")


def _fmt(price: float, digits: int) -> str:
    return f"{price:.{digits}f}"


class OandaBroker(Broker):
    name = "oanda"

    def __init__(self, token: str | None = None, account_id: str | None = None, env: str | None = None,
                 http=None, timeout: float = 15.0):
        self._token = token or os.getenv("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        self.env = (env or os.getenv("OANDA_ENV", "practice")).lower()
        if self.env not in HOSTS:
            raise BrokerError("OANDA_ENV must be 'practice' or 'live'")
        self.is_live = self.env == "live"
        self.base = HOSTS[self.env]
        self._http = http or self._urllib
        self._timeout = timeout
        self._connected = False
        self._instruments: dict[str, dict] = {}
        self._account_ccy = ""

    def __repr__(self) -> str:  # never show the token
        return f"OandaBroker(env={self.env}, account={self.account_id[:7]}***)"

    # ---------------------------------------------------------------- http
    def _urllib(self, method: str, path: str, body: dict | None, params: dict | None) -> tuple[int, dict]:
        url = self.base + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (socket.timeout, TimeoutError) as e:
            raise BrokerTimeout("request timed out: the result is unknown") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise BrokerTimeout("request timed out: the result is unknown") from e
            raise BrokerConnectionError("cannot reach OANDA") from e

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        if not self._token or not self.account_id:
            raise BrokerConnectionError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID are not set")
        status, data = self._http(method, path, body, params)
        if status == 401 or status == 403:
            raise BrokerError("OANDA refused the credentials (check token, account id and OANDA_ENV)")
        if status >= 500:
            raise BrokerConnectionError(f"OANDA server error {status}")
        if status >= 400:
            msg = data.get("errorMessage") or data.get("rejectReason") or f"HTTP {status}"
            raise BrokerError(f"OANDA: {msg}")
        return data

    def _acc(self, suffix: str) -> str:
        return f"/v3/accounts/{self.account_id}{suffix}"

    # ---------------------------------------------------------------- connection
    def connect(self) -> None:
        d = self._call("GET", self._acc("/summary"))
        self._account_ccy = d["account"]["currency"]
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _need(self) -> None:
        if not self._connected:
            raise BrokerConnectionError("not connected")

    # ---------------------------------------------------------------- account
    def account(self) -> AccountInfo:
        self._need()
        a = self._call("GET", self._acc("/summary"))["account"]
        nav, used = float(a["NAV"]), float(a["marginUsed"])
        return AccountInfo(self.name, self.account_id, a["currency"], float(a["balance"]), nav, used,
                           float(a["marginAvailable"]), (nav / used * 100) if used > 0 else None, self.is_live,
                           float(a.get("unrealizedPL", 0.0)), pd.Timestamp.now(tz="UTC"))

    def _position_from(self, t: dict) -> Position:
        units = float(t["currentUnits"])
        sl = t.get("stopLossOrder", {}).get("price")
        tp = t.get("takeProfitOrder", {}).get("price")
        return Position(t["id"], from_oanda(t["instrument"]), 1 if units > 0 else -1, abs(units) / LOT_UNITS,
                        float(t["price"]), float(sl) if sl else None, float(tp) if tp else None,
                        pd.Timestamp(t["openTime"]), float(t.get("unrealizedPL", 0.0)), float(t.get("financing", 0.0)),
                        float(t.get("commission", 0.0)) if "commission" in t else 0.0,
                        t.get("clientExtensions", {}).get("id", ""))

    def positions(self) -> list[Position]:
        self._need()
        d = self._call("GET", self._acc("/openTrades"))
        return [self._position_from(t) for t in d.get("trades", [])]

    def pending_orders(self) -> list[PendingOrder]:
        self._need()
        d = self._call("GET", self._acc("/pendingOrders"))
        out = []
        for o in d.get("orders", []):
            if o.get("type") not in ("LIMIT", "STOP", "MARKET_IF_TOUCHED"):
                continue   # stop-loss / take-profit orders attached to trades are not entries
            units = float(o["units"])
            out.append(PendingOrder(o["id"], from_oanda(o["instrument"]), 1 if units > 0 else -1, abs(units) / LOT_UNITS,
                                    float(o["price"]), float(o["stopLossOnFill"]["price"]) if "stopLossOnFill" in o else None,
                                    float(o["takeProfitOnFill"]["price"]) if "takeProfitOnFill" in o else None,
                                    o.get("clientExtensions", {}).get("id", ""), o["type"].lower()))
        return out

    # ---------------------------------------------------------------- instruments and prices
    def _instrument(self, symbol: str) -> dict:
        if symbol not in self._instruments:
            d = self._call("GET", self._acc("/instruments"), params={"instruments": to_oanda(symbol)})
            if not d.get("instruments"):
                raise BrokerError(f"OANDA does not offer {symbol} on this account")
            self._instruments[symbol] = d["instruments"][0]
        return self._instruments[symbol]

    def _pricing(self, symbol: str) -> dict:
        d = self._call("GET", self._acc("/pricing"), params={"instruments": to_oanda(symbol), "includeHomeConversions": "true"})
        if not d.get("prices"):
            raise BrokerError(f"no price for {symbol}")
        return d

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._need()
        ins = self._instrument(symbol)
        pip = 10 ** int(ins["pipLocation"])
        digits = int(ins["displayPrecision"])
        tick = 10 ** -digits
        prec = int(ins.get("tradeUnitsPrecision", 0))
        step_units = 10 ** -prec
        base, quote = to_oanda(symbol).split("_")
        pr = self._pricing(symbol)
        loss_factor = 1.0 if quote == self._account_ccy else self._home_loss_factor(pr, quote)
        return SymbolSpec(symbol, base, quote, digits, pip, tick, float(LOT_UNITS), float(ins["minimumTradeSize"]) / LOT_UNITS,
                          float(ins["maximumOrderUnits"]) / LOT_UNITS, step_units / LOT_UNITS,
                          LOT_UNITS * tick * loss_factor, 0.0, 0.0)

    @staticmethod
    def _home_loss_factor(pricing: dict, ccy: str) -> float:
        """How many account-currency units one unit of `ccy` is worth when it is a LOSS (OANDA homeConversions)."""
        for c in pricing.get("homeConversions", []):
            if c["currency"] == ccy:
                return float(c["accountLoss"])
        # when the quote currency IS the account currency OANDA may omit it
        raise BrokerError(f"OANDA gave no home conversion for {ccy}; cannot value a pip safely")

    def quote(self, symbol: str) -> Quote:
        self._need()
        p = self._pricing(symbol)["prices"][0]
        if p.get("tradeable") is False:
            raise BrokerError(f"{symbol} is not tradeable right now")
        return Quote(symbol, float(p["bids"][0]["price"]), float(p["asks"][0]["price"]), pd.Timestamp(p["time"]))

    def margin_required(self, symbol: str, volume: float, price: float) -> float:
        ins = self._instrument(symbol)
        pr = self._pricing(symbol)
        base = to_oanda(symbol).split("_")[0]
        if base == self._account_ccy:
            return volume * LOT_UNITS * float(ins["marginRate"])
        for c in pr.get("homeConversions", []):
            if c["currency"] == base:
                return volume * LOT_UNITS * float(c["positionValue"]) * float(ins["marginRate"])
        raise BrokerError(f"OANDA gave no home conversion for {base}; cannot compute margin")

    # ---------------------------------------------------------------- trading
    def place_order(self, req: OrderRequest) -> OrderResult:
        self._need()
        ins = self._instrument(req.symbol)
        digits = int(ins["displayPrecision"])
        units = int(round(req.volume * LOT_UNITS)) * (1 if req.side > 0 else -1)
        order: dict = {"instrument": to_oanda(req.symbol), "units": str(units),
                       "clientExtensions": {"id": req.client_id, "tag": "fxbot"},
                       "tradeClientExtensions": {"id": req.client_id, "tag": "fxbot"}}
        if req.kind == "market":
            order.update(type="MARKET", timeInForce="FOK", positionFill="DEFAULT")
        else:
            order.update(type="LIMIT", price=_fmt(float(req.price), digits), timeInForce="GTC", positionFill="DEFAULT")
        if req.sl is not None:
            order["stopLossOnFill"] = {"price": _fmt(req.sl, digits), "timeInForce": "GTC"}
        if req.tp is not None:
            order["takeProfitOnFill"] = {"price": _fmt(req.tp, digits), "timeInForce": "GTC"}
        d = self._call("POST", self._acc("/orders"), body={"order": order})
        fill = d.get("orderFillTransaction")
        if fill:
            opened = fill.get("tradeOpened") or {}
            return OrderResult(True, "filled", order_id=fill.get("orderID", ""), position_id=opened.get("tradeID", ""),
                               fill_price=float(fill["price"]), filled_volume=abs(float(fill["units"])) / LOT_UNITS)
        cancel = d.get("orderCancelTransaction")
        if cancel:
            return OrderResult(False, "rejected", message=cancel.get("reason", "cancelled by OANDA"))
        if d.get("orderRejectTransaction"):
            return OrderResult(False, "rejected", message=d["orderRejectTransaction"].get("rejectReason", "rejected"))
        create = d.get("orderCreateTransaction")
        if create:
            return OrderResult(True, "accepted", order_id=create.get("id", ""))
        return OrderResult(False, "rejected", message="unrecognised OANDA response")

    def modify_position(self, position_id: str, sl: float | None = None, tp: float | None = None) -> None:
        self._need()
        t = self._call("GET", self._acc(f"/trades/{position_id}"))["trade"]
        digits = int(self._instrument(from_oanda(t["instrument"]))["displayPrecision"])
        body: dict = {}
        if sl is not None:
            body["stopLoss"] = {"price": _fmt(sl, digits), "timeInForce": "GTC"}
        if tp is not None:
            body["takeProfit"] = {"price": _fmt(tp, digits), "timeInForce": "GTC"}
        if not body:
            return
        d = self._call("PUT", self._acc(f"/trades/{position_id}/orders"), body=body)
        if d.get("stopLossOrderRejectTransaction") or d.get("takeProfitOrderRejectTransaction"):
            raise BrokerError("OANDA rejected the stop/target change")

    def cancel_order(self, order_id: str) -> None:
        self._need()
        self._call("PUT", self._acc(f"/orders/{order_id}/cancel"))

    def close_position(self, position_id: str, volume: float | None = None) -> OrderResult:
        self._need()
        units = "ALL" if volume is None else str(int(round(volume * LOT_UNITS)))
        d = self._call("PUT", self._acc(f"/trades/{position_id}/close"), body={"units": units})
        fill = d.get("orderFillTransaction")
        if fill:
            return OrderResult(True, "filled", order_id=fill.get("orderID", ""), position_id=position_id,
                               fill_price=float(fill["price"]), filled_volume=abs(float(fill["units"])) / LOT_UNITS,
                               message=f"pnl {fill.get('pl', '')}")
        reason = (d.get("orderRejectTransaction") or d.get("orderCancelTransaction") or {}).get("reason", "close not confirmed")
        return OrderResult(False, "rejected", message=str(reason))

    # ---------------------------------------------------------------- reconciliation
    def find_by_client_id(self, client_id: str):
        self._need()
        for t in self._call("GET", self._acc("/openTrades")).get("trades", []):
            if t.get("clientExtensions", {}).get("id") == client_id:
                return ("position", self._position_from(t))
        for o in self.pending_orders():
            if o.comment == client_id:
                return ("order", o)
        d = self._call("GET", self._acc("/trades"), params={"state": "CLOSED", "count": 50})
        for t in d.get("trades", []):
            if t.get("clientExtensions", {}).get("id") == client_id:
                ci = self.closed_info(t["id"])
                if ci:
                    return ("closed", ci)
        return None

    def closed_info(self, position_id: str) -> ClosedInfo | None:
        self._need()
        t = self._call("GET", self._acc(f"/trades/{position_id}"))["trade"]
        if t.get("state") != "CLOSED":
            return None
        reason = "unknown"
        if t.get("stopLossOrder", {}).get("state") == "FILLED":
            reason = "sl"
        elif t.get("takeProfitOrder", {}).get("state") == "FILLED":
            reason = "tp"
        pnl = float(t.get("realizedPL", 0.0)) + float(t.get("financing", 0.0))
        return ClosedInfo(position_id, float(t.get("averageClosePrice", 0.0)), pnl, pd.Timestamp(t["closeTime"]), reason)

    def convert(self, amount: float, frm: str, to: str) -> float:
        raise BrokerError("oanda: use the symbol specification (it already values ticks in the account currency)")
