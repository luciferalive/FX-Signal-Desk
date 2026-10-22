"""Multi-timeframe workflow: 1h PLANS, 15m CONFIRMS THE SETUP, 5m EXECUTES, 1m is OPTIONAL precision.

    plan()      1h  -> trend, regime, swing levels, directional bias: BUY / SELL / NEUTRAL / NO TRADE
    setups()    15m -> run the enabled strategies, keep fresh valid setups that the bias allows
    execute()   5m  -> entry confirmation, stop and entry refinement, trade invalidation
    evaluate()  all -> bias -> setup -> trigger -> risk -> final answer: BUY / SELL / WAIT / NO TRADE

The bias never triggers a trade by itself. WAIT and NO TRADE are valid answers and the engine never forces a trade.
Pass only CLOSED candles. The timeframes are configurable (config.json "workflow"); a strategy that does not need
all four timeframes still works because the workflow only asks each strategy for the 15m view.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .analysis import trend_state
from .backtest import pip_size
from .confluence import combine
from .config import load as load_config, enabled_strategies, spread_pips
from .indicators import atr, pivot_idx
from .news import upcoming_high
from .regime import current_regime
from .risk import dedupe, invalidated, position_size, validate
from .sessions import session_labels
from .strategies import STRATEGIES, FAMILIES

NEUTRAL_OK = {"mean_reversion", "price_action", "ict"}  # families allowed when the 1h has no clear direction
MIN_BARS = {"plan": 260, "setup": 300, "execute": 40}
WINDOW = 1500  # bars handed to strategies (keeps scans fast; strategies need <= 300 bars of warm-up)


def _dec(symbol: str) -> int:
    p = pip_size(symbol)
    return 3 if p == 0.01 else 2 if p == 0.1 else 5


# ------------------------------------------------------------------ 1. plan (1h)
def plan(df: pd.DataFrame, symbol: str) -> dict:
    """Directional bias from the planning timeframe."""
    if df is None or len(df) < MIN_BARS["plan"]:
        return dict(bias="NO TRADE", trend=None, regime=None, levels=[], reasons=["not enough 1h candles to plan"])
    tr, rg = trend_state(df), current_regime(df)
    a = float(atr(df).iloc[-1])
    price = float(df["close"].iloc[-1])
    hi, lo = pivot_idx(df["high"].to_numpy(), 3, "max")[-3:], pivot_idx(df["low"].to_numpy(), 3, "min")[-3:]
    levels = [dict(kind="resistance", price=float(df["high"].iloc[i]), t=df.index[i].isoformat()) for i in hi
              if df["high"].iloc[i] > price] + \
             [dict(kind="support", price=float(df["low"].iloc[i]), t=df.index[i].isoformat()) for i in lo
              if df["low"].iloc[i] < price]
    for L in levels:
        L["zone"] = [L["price"] - 0.25 * a, L["price"] + 0.25 * a]
    reasons = [f"1h trend is {tr['label'].lower()} ({tr['structure']})",
               f"1h regime is {rg['label']}" + (f" (ADX {rg['adx']})" if rg["adx"] is not None else "")]
    if rg["label"] == "volatile":
        bias = "NO TRADE"
        reasons.append("volatility is far above normal: stand aside")
    elif tr["label"] == "Bullish" and rg["label"] in ("trending", "transition"):
        bias = "BUY"
    elif tr["label"] == "Bearish" and rg["label"] in ("trending", "transition"):
        bias = "SELL"
    else:
        bias = "NEUTRAL"
        reasons.append("no clear direction: only range / price-action ideas are allowed")
    return dict(bias=bias, trend=tr, regime=rg, levels=levels, atr=a, reasons=reasons)


# ------------------------------------------------------------------ 2. setup (15m)
def setups(df: pd.DataFrame, symbol: str, bias: str, cfg: dict, enabled: list[str] | None = None, mode: str = "scalp"):
    """(valid signals, rejected messages). Only signals from the last `setup_max_age_bars` closed bars count."""
    enabled = enabled or enabled_strategies(cfg)
    age = cfg["workflow"]["setup_max_age_bars"]
    d = df.iloc[-WINDOW:]
    last = len(d) - 1
    found, rejected = [], []
    for name in enabled:
        cls = STRATEGIES[name]
        if mode not in cls.PRESETS:
            continue
        try:
            sigs = cls(mode).generate(d, symbol)
        except Exception as e:  # one broken strategy must not stop the rest
            rejected.append(f"{name}: error {type(e).__name__}")
            continue
        for s in dedupe(sigs, cfg["risk"]["dedupe_bars"]):
            if s.idx < last - age + 1:
                continue
            why = invalidated(s, d)
            if why:
                rejected.append(f"{cls.label} {s.direction}: {why}")
                continue
            if bias == "NO TRADE":
                continue
            if (bias == "BUY" and s.side == -1) or (bias == "SELL" and s.side == 1):
                rejected.append(f"{cls.label} {s.direction}: against the 1h {bias.lower()} bias")
                continue
            if bias == "NEUTRAL" and s.family not in NEUTRAL_OK:
                rejected.append(f"{cls.label} {s.direction}: {FAMILIES.get(s.family, s.family)} needs a clear 1h direction")
                continue
            found.append(s)
    return found, rejected


# ------------------------------------------------------------------ 3. execute (5m, optional 1m)
def execute(df5: pd.DataFrame, sig, a_setup: float, cfg: dict, df1: pd.DataFrame | None = None) -> dict:
    """Entry confirmation on the 5m. Returns dict(ready, entry, sl, notes, wait)."""
    wf, side = cfg["workflow"], sig.side
    risk0 = (sig.entry - sig.sl) * side
    o, h, l, c = (df5[k].to_numpy() for k in ("open", "high", "low", "close"))
    a5 = float(atr(df5).iloc[-1])
    notes = []
    sig_time = pd.Timestamp(sig.time)
    after = df5.index > sig_time
    px = float(c[-1])

    if sig.entry_type == "limit":
        touched = bool(((l[after] <= sig.entry) if side == 1 else (h[after] >= sig.entry)).any()) if after.any() else False
        if not touched:
            return dict(ready=False, wait=f"limit entry {sig.entry:.{_dec(sig.symbol)}f} not reached yet", notes=notes)
        notes.append("price has reached the limit zone")
        entry = float(sig.entry)
    else:
        moved = (px - sig.entry) * side / risk0
        if moved > wf["chase_r"]:
            return dict(ready=False, wait=f"price already ran {moved:.1f}R from the setup entry: wait for a pullback", notes=notes)
        entry = px

    # 5m confirmation: last closed candle goes our way and takes out the previous candle's extreme
    ok = (c[-1] > o[-1] and c[-1] > h[-2]) if side == 1 else (c[-1] < o[-1] and c[-1] < l[-2])
    if not ok:
        return dict(ready=False, wait="waiting for a 5m candle that closes in the trade direction through the previous high/low",
                    notes=notes)
    notes.append("5m candle confirmed: closed " + ("up through the previous high" if side == 1 else "down through the previous low"))

    # optional 1m precision: only refines an existing setup, never creates a trade
    if wf.get("use_precision"):
        if df1 is None or len(df1) < 30:
            notes.append("1m precision requested but 1m data is missing, skipped")
        else:
            o1, h1, l1, c1 = (df1[k].to_numpy() for k in ("open", "high", "low", "close"))
            ok1 = (c1[-1] > o1[-1] and c1[-1] > h1[-2]) if side == 1 else (c1[-1] < o1[-1] and c1[-1] < l1[-2])
            if not ok1:
                return dict(ready=False, wait="setup is valid, waiting for the 1m confirmation candle", notes=notes)
            entry = float(c1[-1]) if sig.entry_type == "market" else entry
            notes.append("1m confirmation candle agrees (precision entry)")

    # stop refinement from 5m structure: allowed only if it is TIGHTER than the setup stop and not noise-tight
    sl = float(sig.sl)
    seg = df5.iloc[-30:]
    piv = pivot_idx(seg["low"].to_numpy() if side == 1 else seg["high"].to_numpy(), 2, "min" if side == 1 else "max")
    if piv:
        j = piv[-1]
        cand = float(seg["low"].iloc[j] - 0.2 * a5) if side == 1 else float(seg["high"].iloc[j] + 0.2 * a5)
        tighter = (cand > sl) if side == 1 else (cand < sl)
        if tighter and (entry - cand) * side >= 1.0 * a5 and (entry - cand) * side > 0:
            notes.append(f"stop refined to the last 5m swing ({cand:.{_dec(sig.symbol)}f}) instead of {sl:.{_dec(sig.symbol)}f}")
            sl = cand
    if (entry - sl) * side <= 0:
        return dict(ready=False, wait="stop would be on the wrong side after refinement", notes=notes)
    return dict(ready=True, entry=entry, sl=sl, notes=notes, wait=None)


# ------------------------------------------------------------------ 4. everything together
def evaluate(symbol: str, frames: dict, cfg: dict | None = None, events: list | None = None,
             now: pd.Timestamp | None = None, enabled: list[str] | None = None) -> dict:
    """frames: {timeframe: closed candles}. Returns a dict with state BUY / SELL / WAIT / NO TRADE and every stage."""
    cfg = cfg or load_config()
    wf = cfg["workflow"]
    now = now or pd.Timestamp.now(tz="UTC")
    dec = _dec(symbol)
    tf = dict(plan=wf["plan"], setup=wf["setup"], execute=wf["execute"], precision=wf["precision"])
    out = dict(symbol=symbol, state="NO TRADE", timeframes=tf, stages={}, reasons=[], rejected=[], signal=None, bias=None)
    stages = out["stages"]

    def finish(state, reason=None):
        out["state"] = state
        if reason:
            out["reasons"].append(reason)
        return out

    df_p, df_s, df_e, df_m = (frames.get(tf[k]) for k in ("plan", "setup", "execute", "precision"))
    missing = [tf[k] for k, d in (("plan", df_p), ("setup", df_s), ("execute", df_e))
               if d is None or len(d) < MIN_BARS[k]]
    if missing:
        stages["plan"] = dict(status="fail", text=f"missing or too short: {', '.join(missing)}")
        return finish("NO TRADE", f"not enough data on {', '.join(missing)}")

    warn = upcoming_high(events or [], symbol, now) if events else None
    if warn:
        stages["news"] = dict(status="fail", text=warn)

    # 1 plan
    pl = plan(df_p, symbol)
    out["bias"] = pl["bias"]
    out["plan"] = dict(bias=pl["bias"], trend=pl["trend"], regime=pl["regime"], reasons=pl["reasons"],
                       levels=pl["levels"][:6])
    stages["plan"] = dict(status="pass" if pl["bias"] in ("BUY", "SELL") else "wait" if pl["bias"] == "NEUTRAL" else "fail",
                          text=f"{tf['plan']} bias {pl['bias']}: " + "; ".join(pl["reasons"]))
    if warn:
        return finish("NO TRADE", warn)
    if pl["bias"] == "NO TRADE":
        return finish("NO TRADE", "; ".join(pl["reasons"]))

    # 2 setup
    found, rejected = setups(df_s, symbol, pl["bias"], cfg, enabled)
    out["rejected"] = rejected
    reg15 = current_regime(df_s)
    sess = str(session_labels(df_s.index)[-1])
    in_kz = sess in ("London KZ", "NY AM KZ", "NY PM KZ")
    best = combine(found, reg15["label"], pl["bias"] if pl["bias"] in ("BUY", "SELL") else "", in_kz)
    if not best["side"]:
        stages["setup"] = dict(status="wait", text=f"no valid {tf['setup']} setup right now")
        return finish("WAIT", "no valid setup on " + tf["setup"] + " yet")
    sig = max(best["signals"], key=lambda s: s.rr)
    stages["setup"] = dict(status="pass", text=f"{tf['setup']} setup: " + ", ".join(sorted({STRATEGIES[s.strategy].label for s in best["signals"]})))

    # 3 trigger
    a15 = float(atr(df_s).iloc[-1])
    ex = execute(df_e, sig, a15, cfg, df_m)
    if not ex["ready"]:
        stages["trigger"] = dict(status="wait", text=ex["wait"])
        out["setup"] = dict(strategy=sig.strategy, direction=sig.direction, entry=sig.entry, sl=sig.sl, tp=sig.tp)
        return finish("WAIT", ex["wait"])
    stages["trigger"] = dict(status="pass", text="; ".join(ex["notes"]) or "confirmed")

    # 4 risk
    entry, sl = ex["entry"], ex["sl"]
    risk = (entry - sl) * sig.side
    rr_final = (sig.tp - entry) * sig.side / risk if risk > 0 else 0
    ok, why = validate(dict(side=sig.side, entry=entry, sl=sl, tp=sig.tp), a15, symbol, cfg, sess,
                       reg15["vol_ratio"], sig.family)
    if not ok:
        stages["risk"] = dict(status="fail", text="; ".join(why))
        out["rejected"] = out["rejected"] + [f"{sig.direction} {STRATEGIES[sig.strategy].label}: {w}" for w in why]
        return finish("NO TRADE", "risk check failed: " + "; ".join(why))
    stages["risk"] = dict(status="pass", text=f"risk {risk / pip_size(symbol):.1f} pips, RR {rr_final:.2f}")

    # final
    size = position_size(cfg["risk"]["account"], cfg["risk"]["risk_pct"], entry, sl, symbol)
    cls = STRATEGIES[sig.strategy]
    expl = [sig.reason,
            f"{tf['plan']} bias is {pl['bias']} ({pl['trend']['label'].lower()}, {pl['regime']['label']})",
            *best["notes"], *ex["notes"]]
    expl += [f"also supported by {STRATEGIES[s.strategy].label}" for s in best["signals"] if s is not sig][:3]
    if best["opposing"]:
        expl.append("note: " + ", ".join(best["opposing"]) + " ideas point the other way")
    signal = dict(
        instrument=symbol, direction=sig.direction, strategy=cls.label, strategy_key=sig.strategy, family=sig.family,
        timeframe=f"{tf['setup']} setup, {tf['execute']} entry (plan {tf['plan']})",
        entry=round(entry, dec), entry_zone=[round(min(entry, sig.entry), dec), round(max(entry, sig.entry), dec)],
        stop_loss=round(sl, dec), take_profit=[round(float(entry + sig.side * risk), dec), round(float(sig.tp), dec)],
        risk_reward=round(rr_final, 2), risk_pips=round(risk / pip_size(symbol), 1),
        confidence=best["score"], confidence_note="confluence score (how much independent evidence lines up), not a win probability",
        regime=reg15["label"], timestamp=pd.Timestamp(df_e.index[-1]).isoformat(), invalidation=sig.invalidation,
        explanation=expl, position=size, rejection_reasons=out["rejected"][:5])
    out["signal"] = signal
    out["setup"] = dict(strategy=sig.strategy, direction=sig.direction, entry=sig.entry, sl=sig.sl, tp=sig.tp)
    return finish(sig.direction)
