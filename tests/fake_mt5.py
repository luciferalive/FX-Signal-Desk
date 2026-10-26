"""A fake `MetaTrader5` module that follows the documented function names, for testing fxbot.trading.mt5 without a
terminal. It is NOT the real package and proves nothing about a real XM terminal."""
from types import SimpleNamespace as NS


class FakeMT5:
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 5, 6, 8
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 0, 1, 2, 3
    ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE_PARTIAL = 10009, 10008, 10010
    TRADE_RETCODE_TIMEOUT, TRADE_RETCODE_CONNECTION, TRADE_RETCODE_REJECT = 10012, 10031, 10006
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN, ORDER_TIME_GTC = 0, 1, 2, 0
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
    DEAL_REASON_CLIENT, DEAL_REASON_SL, DEAL_REASON_TP = 0, 4, 5

    def __init__(self, now_utc: float, server_offset_s: int = 7200, live: bool = False, suffix: str = ""):
        self.now = now_utc                      # real UTC epoch
        self.off = server_offset_s              # server time = utc + off
        self.live = live
        self.suffix = suffix
        self.balance = 10_000.0
        self.positions, self.deals, self.orders, self.ticks = [], [], [], {}
        self.trade_allowed = True
        self.next_ticket = 1000
        self.order_send_none = 0
        self.retcode_override = None
        self.sent = []
        self.initialize_ok = True
        self.logged_in = True

    # ---- setup helpers
    def set_tick(self, symbol, bid, ask, stale_s=0):
        self.ticks[symbol] = NS(bid=bid, ask=ask, time=int(self.now - stale_s + self.off))

    def move(self, symbol, bid, ask):
        self.set_tick(symbol, bid, ask)
        for p in list(self.positions):
            if p.symbol != symbol:
                continue
            exit_px = bid if p.type == 0 else ask
            sign = 1 if p.type == 0 else -1
            if p.sl and sign * (exit_px - p.sl) <= 0:
                self._close(p, p.volume, p.sl, self.DEAL_REASON_SL)
            elif p.tp and sign * (exit_px - p.tp) >= 0:
                self._close(p, p.volume, p.tp, self.DEAL_REASON_TP)

    def _profit(self, p, price, vol):
        sign = 1 if p.type == 0 else -1
        return sign * (price - p.price_open) * vol * 100000 / (150.0 if p.symbol.endswith("JPY") else 1.0)

    def _close(self, p, vol, price, reason):
        d = NS(ticket=self._tk(), position_id=p.ticket, comment="", entry=self.DEAL_ENTRY_OUT, reason=reason, price=price,
               volume=vol, profit=self._profit(p, price, vol), commission=-0.5, swap=0.0, fee=0.0, time=int(self.now + self.off))
        self.deals.append(d)
        if vol >= p.volume - 1e-12:
            self.positions.remove(p)
        else:
            p.volume = round(p.volume - vol, 8)
        return d

    def _tk(self):
        self.next_ticket += 1
        return self.next_ticket

    # ---- documented API
    def initialize(self, *a, **k):
        return self.initialize_ok

    def shutdown(self):
        return True

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        if not self.logged_in:
            return None
        eq = self.balance + sum(p.profit for p in self.positions)
        used = 100.0 * len(self.positions)
        return NS(login=5550001, server="XMGlobal-MT5 test", currency="USD", balance=self.balance, equity=eq, margin=used,
                  margin_free=eq - used, margin_level=(eq / used * 100 if used else 0.0), profit=eq - self.balance,
                  trade_mode=self.ACCOUNT_TRADE_MODE_REAL if self.live else self.ACCOUNT_TRADE_MODE_DEMO)

    def terminal_info(self):
        return NS(trade_allowed=self.trade_allowed)

    def symbol_info(self, symbol):
        base = symbol[:-len(self.suffix)] if self.suffix and symbol.endswith(self.suffix) else symbol
        if base not in ("EURUSD", "GBPJPY", "XAUUSD"):
            return None
        jpy = base.endswith("JPY")
        return NS(point=0.001 if jpy else 0.00001, digits=3 if jpy else 5, trade_tick_size=0.001 if jpy else 0.00001,
                  trade_tick_value=0.667 if jpy else 1.0, trade_tick_value_loss=0.667 if jpy else 1.0,
                  trade_contract_size=100000.0, volume_min=0.01, volume_max=50.0, volume_step=0.01, trade_stops_level=10,
                  trade_freeze_level=0, currency_base=base[:3], currency_profit=base[3:], visible=True, filling_mode=2)

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info_tick(self, symbol):
        return self.ticks.get(symbol)

    def positions_get(self, ticket=None, symbol=None):
        res = [p for p in self.positions if (ticket is None or p.ticket == ticket) and (symbol is None or p.symbol == symbol)]
        return tuple(res) if res else None

    def orders_get(self, **k):
        return tuple(self.orders) or None

    def order_calc_margin(self, action, symbol, volume, price):
        return volume * 1000.0

    def history_deals_get(self, *a, position=None):
        res = [d for d in self.deals if position is None or d.position_id == position]
        return tuple(res)

    def order_send(self, r):
        self.sent.append(dict(r))
        if self.order_send_none:
            self.order_send_none -= 1
            return None
        if self.retcode_override:
            code = self.retcode_override
            return NS(retcode=code, comment="override", order=0, price=0.0, volume=0.0)
        a = r["action"]
        if a == self.TRADE_ACTION_DEAL and "position" not in r:
            t = self._tk()
            p = NS(ticket=t, symbol=r["symbol"], type=r["type"], volume=r["volume"], price_open=r["price"], sl=r["sl"], tp=r["tp"],
                   time=int(self.now + self.off), profit=0.0, swap=0.0, comment=r["comment"], magic=r["magic"])
            self.positions.append(p)
            self.deals.append(NS(ticket=self._tk(), position_id=t, comment=r["comment"], entry=0, reason=0, price=r["price"],
                                 volume=r["volume"], profit=0.0, commission=-0.5, swap=0.0, fee=0.0, time=p.time))
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="done", order=t, price=r["price"], volume=r["volume"])
        if a == self.TRADE_ACTION_DEAL:
            p = next((x for x in self.positions if x.ticket == r["position"]), None)
            if not p:
                return NS(retcode=10013, comment="invalid", order=0, price=0.0, volume=0.0)
            self._close(p, r["volume"], r["price"], self.DEAL_REASON_CLIENT)
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="done", order=self._tk(), price=r["price"], volume=r["volume"])
        if a == self.TRADE_ACTION_SLTP:
            p = next((x for x in self.positions if x.ticket == r["position"]), None)
            if not p:
                return NS(retcode=10013, comment="invalid", order=0, price=0.0, volume=0.0)
            p.sl, p.tp = r["sl"], r["tp"]
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="done", order=0, price=0.0, volume=0.0)
        if a == self.TRADE_ACTION_PENDING:
            t = self._tk()
            self.orders.append(NS(ticket=t, symbol=r["symbol"], type=r["type"], volume_current=r["volume"], price_open=r["price"],
                                  sl=r["sl"], tp=r["tp"], comment=r["comment"]))
            return NS(retcode=self.TRADE_RETCODE_PLACED, comment="placed", order=t, price=r["price"], volume=r["volume"])
        if a == self.TRADE_ACTION_REMOVE:
            self.orders = [o for o in self.orders if o.ticket != r["order"]]
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="removed", order=r["order"], price=0.0, volume=0.0)
        return NS(retcode=10013, comment="invalid", order=0, price=0.0, volume=0.0)
