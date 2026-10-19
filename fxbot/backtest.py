"""Bar-by-bar backtester. One position at a time, fixed % risk, spread cost included.
If SL and TP are both inside one candle the SL is assumed to hit first (conservative)."""
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
    return 0.0001


@dataclass
class Result:
    stats: dict
    trades: pd.DataFrame
    equity: pd.Series
    by_session: pd.DataFrame


def run_backtest(df, signals: list[Signal], symbol, risk_pct=1.0, spread_pips=1.0, start_equity=10_000.0) -> Result:
    n = len(df)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    spread = spread_pips * pip_size(symbol)
    rows, busy = [], -1

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
                         strategy=s.strategy, mode=s.mode, session=s.session, reason=s.reason))

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
    )
    by_sess = trades.groupby("session")["r"].agg(trades="count", win_rate=lambda x: round(100 * (x > 0).mean(), 1),
                                                  avg_R=lambda x: round(x.mean(), 3))
    return Result(stats, trades, pd.Series(eq, index=trades["exit_time"]), by_sess)


def format_report(res: Result, title: str) -> str:
    out = [f"=== {title} ==="]
    if res.stats.get("trades", 0) == 0:
        return "\n".join(out + ["no trades"])
    out += [f"  {k:>18}: {v}" for k, v in res.stats.items()]
    out += ["  by session:", res.by_session.to_string()]
    return "\n".join(out)
