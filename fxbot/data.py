"""Free data sources: yfinance (no key), CSV files, and a synthetic generator for tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import NY

# yfinance intraday limits: 1m=7d, 5m/15m/30m=60d, 1h=730d
INTERVAL_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "4h": "730d", "1d": "730d",
                   "1w": "10y", "1M": "max"}
_RULE = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}
_YF_INTERVAL = {"1w": "1wk", "1M": "1mo"}  # weekly and monthly candles come straight from Yahoo
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
    if interval in ("1w", "1M"):
        # forex week / month start at 17:00 NY: shift +7h, group by calendar period, shift back
        shift = pd.Timedelta(hours=7)
        local = ny.copy()
        local.index = (local.index + shift).tz_localize(None)
        per = local.index.to_period("W-SUN" if interval == "1w" else "M")
        out = local.groupby(per).agg(agg)
        out.index = (out.index.start_time - shift).tz_localize(NY)
        return out.dropna(subset=["open"]).tz_convert("UTC")
    kw = {"origin": "start_day"}
    if interval in _OFFSET:
        kw["offset"] = _OFFSET[interval]
    out = ny.resample(_RULE[interval], **kw).agg(agg).dropna(subset=["open"])
    return out.tz_convert("UTC")


def fetch(symbol: str, interval: str = "15m", period: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    import time

    base = "1h" if interval in ("4h", "1d") else _YF_INTERVAL.get(interval, interval)
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


def synthetic(interval: str = "15m", days: int = 300, seed: int = 7, base: float = 1.10,
              step_min: int = 15) -> pd.DataFrame:
    """Fake-but-plausible FX bars (session volatility, trend regimes). For tests/demo only.
    step_min sets the candle size of the raw series (1, 5 or 15); `interval` resamples it afterwards."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06 00:00", periods=days * (1440 // step_min), freq=f"{step_min}min", tz="UTC")
    wk = idx.dayofweek
    idx = idx[~((wk == 5) | ((wk == 6) & (idx.hour < 22)) | ((wk == 4) & (idx.hour >= 22)))]
    n = len(idx)
    ny = idx.tz_convert(NY)
    hr = (ny.hour + ny.minute / 60.0).to_numpy()
    season = np.where((hr >= 2) & (hr < 5), 1.8, np.where((hr >= 7) & (hr < 10), 2.0, np.where((hr >= 19) | (hr < 2), 0.6, 1.0)))
    k = (step_min / 15) ** 0.5  # volatility scales with the square root of candle length
    vol = 0.00025 * season * k
    drift = np.sign(np.sin(np.arange(n) / (96 * 8 * 15 / step_min))) * 0.00002 * (step_min / 15)
    close = base * np.exp(np.cumsum(rng.normal(drift, vol)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, vol * 0.6))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    return df if interval == f"{step_min}m" else resample(df, interval)


# ---------------------------------------------------------------- bulk download for the dashboard
def split_bulk(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Turn one multi-ticker yfinance frame into {ticker: clean OHLC frame}. Handles either column level order."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    cols = raw.columns
    if not isinstance(cols, pd.MultiIndex):
        if len(tickers) == 1:
            try:
                out[tickers[0]] = _clean(raw)
            except Exception:
                pass
        return out
    ticker_level = next((i for i in range(cols.nlevels) if set(tickers) & set(cols.get_level_values(i))), None)
    if ticker_level is None:
        return out
    for t in tickers:
        if t not in set(cols.get_level_values(ticker_level)):
            continue
        sub = raw.xs(t, axis=1, level=ticker_level)
        sub = sub.dropna(how="all")
        if sub.empty:
            continue
        try:
            out[t] = _clean(sub)
        except Exception:
            continue
    return out


def fetch_many(symbols: list[str], interval: str, pause: float = 0.3) -> dict[str, pd.DataFrame]:
    """Download one timeframe for many pairs. A pair that fails is simply missing from the result."""
    import time

    import yfinance as yf

    yf_map = {yf_symbol(s): s for s in symbols}
    tickers = list(yf_map)
    base = "1h" if interval in ("4h", "1d") else _YF_INTERVAL.get(interval, interval)
    got: dict[str, pd.DataFrame] = {}
    for attempt in range(3):
        todo = [t for t in tickers if yf_map[t] not in got]
        if not todo:
            break
        try:
            raw = yf.download(todo, interval=base, period=INTERVAL_PERIOD[interval], group_by="ticker",
                              auto_adjust=False, progress=False, threads=False)
            for t, df in split_bulk(raw, todo).items():
                got[yf_map[t]] = resample(df, interval) if interval in ("4h", "1d") else df
        except Exception as e:
            print(f"[data] bulk {interval} attempt {attempt + 1}: {e}")
        time.sleep(pause * (attempt + 1))
    # last resort: pairs the bulk call missed, one by one
    for t in tickers:
        sym = yf_map[t]
        if sym in got:
            continue
        try:
            got[sym] = fetch(sym, interval)
        except Exception as e:
            print(f"[data] {sym} {interval}: {e}")
        time.sleep(pause)
    return got


def load_frames(symbols: list[str], tfs: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """{symbol: {timeframe: candles}} using the fewest Yahoo downloads:
    30m comes from 15m, and 4H and 1D come from 1h. Weekly and monthly are downloaded directly."""
    derive = {"30m": "15m", "4h": "1h", "1d": "1h"}
    native = {iv: fetch_many(symbols, iv) for iv in sorted({derive.get(t, t) for t in tfs})}
    out: dict[str, dict[str, pd.DataFrame]] = {s: {} for s in symbols}
    for sym in symbols:
        for tf in tfs:
            src = native.get(derive.get(tf, tf), {}).get(sym)
            if src is None or src.empty:
                continue
            out[sym][tf] = resample(src, tf) if tf in derive else src
    return out
