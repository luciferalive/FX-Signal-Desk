"""Free data sources: yfinance (no key), CSV files, and a synthetic generator for tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import NY

# yfinance intraday limits: 1m=7d, 5m/15m/30m=60d, 1h=730d
INTERVAL_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "4h": "730d", "1d": "730d"}
_RULE = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}
# 4H candles anchored to 17:00 NY, daily candles roll at 17:00 NY (same as TradingView forex)
_OFFSET = {"4h": "1h", "1d": "17h"}


def yf_symbol(s: str) -> str:
    s = s.upper().replace("/", "")
    if s.endswith(("=X", "=F")):
        return s
    if s.startswith("XAU"):
        return "GC=F"
    if s.startswith("XAG"):
        return "SI=F"
    return s + "=X"


def _clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=lambda c: str(c).lower())
    if "volume" not in df:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[~df.index.duplicated()].sort_index()


def resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    ny = df.tz_convert(NY)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in ny:
        agg["volume"] = "sum"
    if interval == "1d":
        # forex day rolls at 17:00 NY: shift +7h so the day bucket starts at midnight, then shift back
        shift = pd.Timedelta(hours=7)
        ny = ny.copy()
        ny.index = ny.index + shift
        out = ny.resample("1D").agg(agg).dropna(subset=["open"])
        out.index = out.index - shift
        return out.tz_convert("UTC")
    kw = {"origin": "start_day"}
    if interval in _OFFSET:
        kw["offset"] = _OFFSET[interval]
    out = ny.resample(_RULE[interval], **kw).agg(agg).dropna(subset=["open"])
    return out.tz_convert("UTC")


def fetch(symbol: str, interval: str = "15m", period: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    import time

    base = "1h" if interval in ("4h", "1d") else interval
    raw = None
    for attempt in range(3):  # Yahoo sometimes hiccups, especially from cloud servers
        try:
            raw = yf.download(
                yf_symbol(symbol), interval=base, period=period or INTERVAL_PERIOD[interval],
                auto_adjust=False, progress=False, threads=False,
            )
        except Exception:
            raw = None
        if raw is not None and not raw.empty:
            break
        time.sleep(2 * (attempt + 1))
    if raw is None or raw.empty:
        raise RuntimeError(f"No data for {symbol} {interval} (yfinance returned nothing)")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = _clean(raw)
    return resample(df, interval) if interval in ("4h", "1d") else df


def load_csv(path: str, interval: str | None = None) -> pd.DataFrame:
    """Load Dukascopy / MT5 / TradingView-export style CSVs (time,open,high,low,close[,volume]).
    Timestamps are assumed UTC. Pass interval to resample (e.g. 1-minute CSV -> '15m')."""
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    tcol = next(c for c in ("time", "datetime", "date", "timestamp", "gmt time", "local time") if c in raw.columns)
    raw.index = pd.to_datetime(raw[tcol], utc=True, dayfirst=False)
    df = _clean(raw)
    return resample(df, interval) if interval else df


def synthetic(interval: str = "15m", days: int = 300, seed: int = 7) -> pd.DataFrame:
    """Fake-but-plausible FX bars (session volatility, trend regimes). For tests/demo only."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06 00:00", periods=days * 96, freq="15min", tz="UTC")
    wk = idx.dayofweek
    idx = idx[~((wk == 5) | ((wk == 6) & (idx.hour < 22)) | ((wk == 4) & (idx.hour >= 22)))]
    n = len(idx)
    ny = idx.tz_convert(NY)
    hr = (ny.hour + ny.minute / 60.0).to_numpy()
    season = np.where((hr >= 2) & (hr < 5), 1.8, np.where((hr >= 7) & (hr < 10), 2.0, np.where((hr >= 19) | (hr < 2), 0.6, 1.0)))
    vol = 0.00025 * season
    drift = np.sign(np.sin(np.arange(n) / (96 * 8))) * 0.00002
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, vol)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, vol * 0.6))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    return df if interval == "15m" else resample(df, interval)
