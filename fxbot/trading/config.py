"""Trading settings. DEMO and LIVE are two completely independent profiles: a live run never inherits demo values.

Put a trading.json next to pairs.txt (or set FXBOT_TRADING_CONFIG). Only the keys you write are overridden, inside the
profile you name. Nothing in here is advice: the numbers are cautious starting points, not proven to be profitable.
Credentials are NEVER stored in this file: they come from environment variables (see broker section in the README).
"""
from __future__ import annotations

import copy
import json
import os

LIVE_PHRASE = "I UNDERSTAND LIVE TRADING RISKS MY MONEY"  # typed by a human each time live trading is armed

_COMMON = {
    # sim = built-in simulated account | mt5 = MetaTrader 5 terminal (XM) | oanda = OANDA v20. "xm" is an alias of mt5.
    "broker": {"name": "sim",
               "mt5": {"suffix": "", "symbol_map": {}, "server_utc_offset_hours": None,
                       "commission_per_lot_round_turn": 0.0, "deviation_points": 20}},
    "risk": {
        "risk_pct": 0.25,                 # % of equity risked per trade
        "max_trade_risk_pct": 0.5,        # a single trade may never risk more than this
        "max_total_risk_pct": 1.0,        # all open positions + pending orders together
        "max_daily_loss_pct": 2.0,        # of start-of-day equity, realized + unrealized + costs
        "max_weekly_loss_pct": 4.0,       # of start-of-week equity
        "max_positions": 3,
        "max_positions_per_symbol": 1,
        "max_per_strategy_symbol": 1,
        "max_entries_per_symbol_day": 3,
        "max_currency_risk_pct": 0.75,    # risk leaning the same way on one currency (long EUR, short USD, ...)
        "correlation_groups": [["EURUSD", "GBPUSD"], ["AUDUSD", "NZDUSD"], ["USDCHF", "USDJPY"], ["EURJPY", "GBPJPY"]],
        "min_rr": 1.5,
        "stop_atr_min": 0.4,
        "stop_atr_max": 4.0,
        "max_spread_to_risk": 0.15,       # spread may use at most 15% of the stop distance
        "max_spread_vs_typical": 3.0,     # and at most 3x the pair's typical spread (config.json "spreads")
        "vol_ratio_min": 0.35,
        "vol_ratio_max": 2.5,
        "margin_buffer": 1.5,             # free margin must cover the new trade's margin x this
        "no_stop_risk_pct": 1.0,          # a position or order with NO stop counts as this much risk
        "slippage_pips": 0.3,             # expected slippage, charged in the sizing and the risk calculation
        "max_signal_age_s": 900,          # a signal older than this is never executed
        "chase_r": 0.5,                   # reject if price already moved this fraction of the stop away from the entry
        "max_quote_age_s": 15,            # a quote older than this is stale and never used for an order
    },
    "exits": {
        "tp_fractions": [0.4, 0.3],       # share of the ORIGINAL volume closed at TP1, TP2; the last target closes the rest
        "breakeven": {"enabled": False, "after": "tp1"},   # off by default, never automatic
        "trailing": {"mode": "none", "atr_mult": 2.0, "start_after": "tp1"},   # none | atr | swing | strategy
        "max_hold_hours": {"scalp": 6, "intraday": 30, "swing": None},         # swing trades are never cut by an intraday rule
        "on_stop_failure": "retry_then_close",   # retry_then_close | close | alert_only
    },
    "sessions": {
        "broker_tz": "America/New_York",
        "rollover_hour": 17,
        "allowed": ["London", "New York", "overlap"],   # Asian | London | New York | overlap ; None = any time
        "strategy_sessions": {},                         # e.g. {"sessionbreak": ["London", "New York"]}
        "max_entries_per_session": 2,
        "hold_outside_session": ["swing", "intraday"],   # styles that may stay open after the session ends
        "block_new_before_rollover_min": 30,
        "close_before_rollover": ["scalp"],              # styles closed shortly before rollover
        "close_before_rollover_min": 10,
        "allow_overnight": ["swing", "intraday"],
    },
    "news": {
        "enabled": True,
        "impacts": ["high"],
        "block_before_min": 30,
        "block_after_min": 15,
        "existing_positions": "retain",   # retain | close
    },
    "cooldown_min": {"default": 60, "scalp": 30, "intraday": 60, "swing": 240},   # after a trade closes, per strategy+symbol
    "arbitration": {
        "price_tolerance_atr": 0.5,       # signals this close in price are one idea
        "conflict": "reject",             # reject | priority
        "priority": ["ict", "trendpull", "retest", "donchian", "emacross", "squeeze", "nr7", "setforget", "failbreak",
                     "sessionbreak", "hastoch", "bbrevert", "rsi2"],
    },
    "daily_lock": {"existing_positions": "keep"},   # keep | close_losers | close_all (what to do with open trades when locked)
    "execution": {"order_timeout_s": 15, "unconfirmed_grace_s": 120, "loop_seconds": 30},
}

DEMO = copy.deepcopy(_COMMON)

# LIVE is written out on its own, tighter, and starts with fewer simultaneous trades. It also needs the typed phrase.
LIVE = copy.deepcopy(_COMMON)
LIVE["risk"].update({"risk_pct": 0.1, "max_trade_risk_pct": 0.25, "max_total_risk_pct": 0.5, "max_daily_loss_pct": 1.0,
                     "max_weekly_loss_pct": 2.0, "max_positions": 1, "max_entries_per_symbol_day": 1,
                     "max_currency_risk_pct": 0.25})
LIVE["exits"]["breakeven"] = {"enabled": False, "after": "tp1"}

PROFILES = {"demo": DEMO, "live": LIVE}


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(profile: str = "demo", path: str | None = None) -> dict:
    """The settings of one profile. Returns a fresh copy; the file may override keys but only inside its own profile."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile '{profile}' (use demo or live)")
    cfg = copy.deepcopy(PROFILES[profile])
    path = path or os.getenv("FXBOT_TRADING_CONFIG") or "trading.json"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _merge(cfg, data.get(profile, {}))
        if "broker" in data and "broker" not in data.get(profile, {}):
            _merge(cfg["broker"], data["broker"])
    cfg["profile"] = profile
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    r, e = cfg["risk"], cfg["exits"]
    if r["risk_pct"] > r["max_trade_risk_pct"]:
        raise ValueError(f"risk_pct {r['risk_pct']} is above max_trade_risk_pct {r['max_trade_risk_pct']}")
    if r["max_trade_risk_pct"] > r["max_total_risk_pct"]:
        raise ValueError("max_trade_risk_pct is above max_total_risk_pct")
    if not 0 <= sum(e["tp_fractions"]) < 1:
        raise ValueError("tp_fractions must add up to less than 1 (the last target takes the rest)")
    if cfg["exits"]["on_stop_failure"] not in ("retry_then_close", "close", "alert_only"):
        raise ValueError("on_stop_failure must be retry_then_close, close or alert_only")
    if cfg["daily_lock"]["existing_positions"] not in ("keep", "close_losers", "close_all"):
        raise ValueError("daily_lock.existing_positions must be keep, close_losers or close_all")
    if cfg["news"]["existing_positions"] not in ("retain", "close"):
        raise ValueError("news.existing_positions must be retain or close")
    if cfg["broker"]["name"] == "xm":
        cfg["broker"]["name"] = "mt5"
    if cfg["broker"]["name"] not in ("sim", "oanda", "mt5"):
        raise ValueError("broker.name must be sim, mt5 (XM) or oanda")
