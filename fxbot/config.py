"""Settings. Defaults live here; put a config.json next to pairs.txt (or set FXBOT_CONFIG) to change any of them.
Only the keys you write are overridden, so a two-line file is fine. See config.example.json."""
from __future__ import annotations

import copy
import json
import os

DEFAULTS: dict = {
    # which strategies may produce signals (names from fxbot.strategies.STRATEGIES). None = all of them
    "strategies": {"enabled": None},
    "risk": {
        "account": 10000.0,           # account size used for position sizing
        "risk_pct": 1.0,              # % of the account risked per trade
        "max_risk_pct": 2.0,          # a trade that would risk more than this is rejected
        "min_rr": 1.5,                # minimum reward-to-risk to the final target
        "min_rr_family": {"mean_reversion": 1.0},  # mean reversion targets the middle band, so it gets a lower bar
        "stop_atr_min": 0.4,          # stop must be at least this many ATR away (too tight = noise)
        "stop_atr_max": 4.0,          # ...and at most this many (too wide = bad trade)
        "max_spread_to_risk": 0.15,   # spread may use at most 15% of the stop distance
        "vol_ratio_max": 2.5,         # ATR above 2.5x its normal level = too volatile
        "vol_ratio_min": 0.35,        # ATR below 0.35x normal = dead market
        "sessions": None,             # e.g. ["London KZ", "NY AM KZ"]; None = any session
        "dedupe_bars": 8,             # the same strategy + side within this many bars counts as one signal
    },
    # typical retail spread in pips (used for the spread filter and the backtest); "default" covers the rest
    "spreads": {"default": 1.5, "EURUSD": 0.8, "USDJPY": 0.9, "GBPUSD": 1.1, "AUDUSD": 1.0, "USDCAD": 1.3,
                "USDCHF": 1.3, "NZDUSD": 1.4, "EURJPY": 1.4, "GBPJPY": 2.0, "XAUUSD": 3.0, "XAGUSD": 3.0},
    # multi-timeframe workflow: 1h plans, 15m confirms the setup, 5m executes, 1m is optional precision
    "workflow": {"plan": "1h", "setup": "15m", "execute": "5m", "precision": "1m", "use_precision": False,
                 "setup_max_age_bars": 3, "chase_r": 0.5},
    "backtest": {"slippage_pips": 0.2, "commission_pips": 0.0, "dev_fraction": 0.7},
}


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.getenv("FXBOT_CONFIG") or "config.json"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _merge(cfg, json.load(f))
    return cfg


def spread_pips(cfg: dict, symbol: str) -> float:
    s = cfg["spreads"]
    return float(s.get(symbol, s["default"]))


def enabled_strategies(cfg: dict) -> list[str]:
    from .strategies import STRATEGIES
    names = cfg["strategies"]["enabled"]
    return [n for n in (names if names else STRATEGIES) if n in STRATEGIES]
