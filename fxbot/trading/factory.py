"""Builds the broker named in the trading config. Credentials are read from environment variables by the adapters."""
from __future__ import annotations

from .broker import Broker


def make_broker(cfg: dict) -> Broker:
    name = cfg["broker"]["name"]
    if name == "sim":
        from .sim import SimBroker
        return SimBroker(is_live=False)
    if name == "oanda":
        from .oanda import OandaBroker
        return OandaBroker()
    if name == "mt5":
        from .mt5 import MT5Broker
        o = cfg["broker"].get("mt5", {})
        return MT5Broker(suffix=o.get("suffix", ""), symbol_map=o.get("symbol_map"),
                         server_utc_offset_hours=o.get("server_utc_offset_hours"),
                         commission_per_lot_round_turn=o.get("commission_per_lot_round_turn", 0.0),
                         deviation_points=o.get("deviation_points", 20))
    raise ValueError(f"unknown broker {name}")
