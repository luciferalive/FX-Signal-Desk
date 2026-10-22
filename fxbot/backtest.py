"""Bar-by-bar backtester. One position at a time, fixed % risk, costs included (spread, slippage, commission).

Honest-execution rules:
  * a signal made on bar i is entered on bar i+1 or later (never on the candle that created it);
  * if SL and TP are both inside one candle the SL is assumed to hit first (conservative), and those candles are counted;
  * costs are charged on every trade: spread + 2 x slippage + commission, all in pips.
Use split_backtest to judge a strategy on data it was not tuned on."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategies import Signal


def pip_size(symbol: str) -> float:
    s = symbol.upper().replace("=X", "").replace("/", "")
    if "JPY" in s:
        return 0.01
    if s.startswith("XAU") or s == "GC=F":
        return 0.1
    if s.startswith("XAG"):
        return 0.01
    return 0.0001


@dataclass
class Result:
    stats: dict
    trades: pd.DataFrame
    equity: pd.Series
    by_session: pd.DataFrame


def run_backtest(df, signals: list[Signal], symbol, risk_pct=1.0, spread_pips=1.0, start_equity=10_000.0,
                 slippage_pips=0.0, commission_pips=0.0) -> Result:
    n = len(df)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    spread = (spread_pips + 2 * slippage_pips + commission_pips) * pip_size(symbol)  # total cost per trade in price
    rows, busy, conflicts = [], -1, 0

    for s in sorted(signals, key=lambda x: x.idx):
        if s.idx <= busy:
            continue
        j0 = s.idx + 1
        if j0 >= n:
            continue
        side = s.side
        if s.entry_type == "market":
            fi, fill = j0, o[j0]
        else:
            fi = fill = None
            for j in range(j0, min(j0 + s.expiry, n)):
                if side == 1 and l[j] <= s.entry:
                    fi, fill = j, min(s.entry, o[j])
                    break
                if side == -1 and h[j] >= s.entry:
                    fi, fill = j, max(s.entry, o[j])
                    break
            if fi is None:
                busy = j0 + s.expiry - 1
                continue
        risk = (fill - s.sl) * side
        if risk <= 0 or risk < 2 * spread:
            continue
        tp = fill + side * s.rr * risk

        ei, ex, r, outcome = n - 1, c[-1], None, "open"
        for j in range(fi, n):
            hit_sl = l[j] <= s.sl if side == 1 else h[j] >= s.sl
            hit_tp = h[j] >= tp if side == 1 else l[j] <= tp
            if hit_sl:
                if hit_tp:
                    conflicts += 1  # both inside one candle: unknowable, counted as a loss
                ei, ex, r, outcome = j, s.sl, -1.0, "SL"
                break
            if hit_tp:
                ei, ex, r, outcome = j, tp, s.rr, "TP"
                break
        if r is None:
            r = (ex - fill) * side / risk
        r_net = r - spread / risk
        busy = ei
        rows.append(dict(signal_time=s.time, fill_time=df.index[fi], exit_time=df.index[ei], side=side,
                         fill=fill, sl=s.sl, tp=tp, exit=ex, r=r_net, outcome=outcome,
                         strategy=s.strategy, mode=s.mode, session=s.session, reason=s.reason,
                         regime=s.regime or "unknown", timeframe=s.timeframe or s.mode, family=s.family,
                         planned_rr=s.rr, bars=ei - fi + 1))

    trades = pd.DataFrame(rows)
    if trades.empty:
        return Result(dict(trades=0), trades, pd.Series(dtype=float), pd.DataFrame())

    r = trades["r"].to_numpy()
    eq = start_equity * np.cumprod(1 + risk_pct / 100 * r)
    peak = np.maximum.accumulate(np.r_[start_equity, eq])[1:]
    trades["equity"] = eq
    wins, losses = r[r > 0], r[r <= 0]
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    stats = dict(
        trades=len(r),
        win_rate=round(100 * len(wins) / len(r), 1),
        avg_R=round(float(r.mean()), 3),
        profit_factor=round(float(wins.sum() / abs(losses.sum())), 2) if losses.sum() != 0 else float("inf"),
        total_return_pct=round(float(eq[-1] / start_equity * 100 - 100), 1),
        max_drawdown_pct=round(float((eq / peak - 1).min() * 100), 1),
        max_losing_streak=streak,
        avg_planned_RR=round(float(trades["planned_rr"].mean()), 2),
        avg_trade_bars=round(float(trades["bars"].mean()), 1),
        same_candle_conflicts=conflicts,
    )
    by_sess = trades.groupby("session")["r"].agg(trades="count", win_rate=lambda x: round(100 * (x > 0).mean(), 1),
                                                  avg_R=lambda x: round(x.mean(), 3))
    return Result(stats, trades, pd.Series(eq, index=trades["exit_time"]), by_sess)


def breakdown(trades: pd.DataFrame, col: str) -> pd.DataFrame:
    """Trades, win rate, average R and profit factor grouped by a column (strategy, regime, timeframe, session...)."""
    if trades.empty or col not in trades:
        return pd.DataFrame()
    def pf(x):
        loss = abs(x[x <= 0].sum())
        return round(float(x[x > 0].sum() / loss), 2) if loss else float("inf")
    return trades.groupby(col)["r"].agg(trades="count", win_rate=lambda x: round(100 * (x > 0).mean(), 1),
                                        avg_R=lambda x: round(x.mean(), 3), pf=pf)


def format_report(res: Result, title: str) -> str:
    out = [f"=== {title} ==="]
    if res.stats.get("trades", 0) == 0:
        return "\n".join(out + ["no trades"])
    out += [f"  {k:>22}: {v}" for k, v in res.stats.items()]
    for col in ("strategy", "regime", "timeframe", "session"):
        b = breakdown(res.trades, col)
        if len(b):
            out += [f"  by {col}:", b.to_string()]
    n = res.stats["trades"]
    out.append("  note: " + ("fewer than 30 trades, results are mostly luck" if n < 30 else
                             "fewer than 100 trades, treat as a hint only" if n < 100 else "sample size is usable"))
    return "\n".join(out)


def split_backtest(df, signals: list[Signal], symbol: str, frac: float = 0.7, **kw):
    """Development / evaluation split by time. Tune on the first part only, then look at the second part once.
    Returns (development Result, evaluation Result, cutoff timestamp)."""
    cut = int(len(df) * frac)
    dev = [s for s in signals if s.idx < cut]
    ev = [Signal(**{**s.__dict__, "idx": s.idx - cut}) for s in signals if s.idx >= cut]
    return run_backtest(df.iloc[:cut], dev, symbol, **kw), run_backtest(df.iloc[cut:], ev, symbol, **kw), df.index[cut]


def trade_outcome(df: pd.DataFrame, s: Signal) -> str:
    """What happened to one signal on the candles after it: 'TP', 'SL', 'open' or 'unfilled'.
    Same conservative rule as run_backtest: if SL and TP sit inside one candle, SL is assumed to hit first."""
    n = len(df)
    h, l, o = df["high"].to_numpy(), df["low"].to_numpy(), df["open"].to_numpy()
    j0 = s.idx + 1
    if j0 >= n:
        return "open"
    side = s.side
    if s.entry_type == "market":
        fi, fill = j0, o[j0]
    else:
        fi = fill = None
        for j in range(j0, min(j0 + s.expiry, n)):
            if (side == 1 and l[j] <= s.entry) or (side == -1 and h[j] >= s.entry):
                fi, fill = j, s.entry
                break
        if fi is None:
            return "unfilled" if j0 + s.expiry <= n else "open"
    risk = (fill - s.sl) * side
    if risk <= 0:
        return "unfilled"
    tp = fill + side * s.rr * risk
    for j in range(fi, n):
        if (l[j] <= s.sl) if side == 1 else (h[j] >= s.sl):
            return "SL"
        if (h[j] >= tp) if side == 1 else (l[j] <= tp):
            return "TP"
    return "open"
