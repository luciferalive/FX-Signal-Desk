"""The Telegram signal card: one fact per line, easy to read on a phone. No reasons, only what you need to take the trade."""
from __future__ import annotations

import pandas as pd

from .backtest import pip_size
from .sessions import IST, NY, session_labels

BAR_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
SESSION_NAMES = {
    "Asia": "Asia session",
    "London KZ": "London killzone (best hours for London pairs)",
    "NY AM KZ": "New York morning killzone",
    "London Close": "London close",
    "NY PM KZ": "New York afternoon killzone",
    "Off-hours": "Off-hours (quieter market, wider spreads)",
}
LINE = "━━━━━━━━━━━━━━━━━━"


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def session_of(ts) -> str:
    return SESSION_NAMES.get(str(session_labels(pd.DatetimeIndex([_utc(ts)]))[0]), "Off-hours")


def _clock(t: pd.Timestamp, tz=None, date=False) -> str:
    t = t.tz_convert(tz) if tz else t
    return f"{t:%d %b, }" * date + f"{t:%I:%M %p}".lstrip("0")


def grade_from_score(score: float) -> str:
    return "A" if score >= 75 else "B" if score >= 60 else "C"


def card(*, symbol: str, side: str, strategy: str, grade: str, score_text: str, frames: str, ts, bar_min: int,
         entry: float, entry_type: str, status: str = "", sl: float, tp1: float, tp2: float, dec: int,
         expiry_bars: int = 0, expiry_bar_min: int = 15, pip: float = 0.0001, size: str = "", warn: str = "") -> str:
    buy = side.upper() == "BUY"
    sgn = 1 if buy else -1
    close = _utc(ts) + pd.Timedelta(minutes=bar_min)          # the candle has closed: this is when the signal exists
    risk = abs(entry - sl)
    f = lambda p: f"{p:.{dec}f}"
    pips = lambda p: abs(p - entry) / pip
    r_of = lambda p: abs(p - entry) / risk if risk else 0
    et = (entry_type or "market").lower()
    kind = {"market": "MARKET  (enter now at the current price)",
            "limit": "LIMIT  (place a pending order, do not chase)",
            "stop": "STOP  (pending order that triggers on a break)"}.get(et, et.upper())
    head = "🟢" if buy else "🔴"
    lines = [
        f"{head} {side.upper()}  {symbol}",
        LINE,
        f"⭐ Grade {grade}  ·  {score_text}",
        f"🧩 {strategy}",
        f"📊 {frames}",
        "",
        "🕒 SIGNAL TIME (candle close)",
        f"   IST  {_clock(close, IST, True)}",
        f"   UTC  {_clock(close)}",
        f"   NY   {_clock(close, NY)}",
        f"🌍 Session: {session_of(close)}",
        "",
        f"📍 ENTRY TYPE: {kind}",
        f"   Entry   {f(entry)}",
        f"🛑 SL      {f(sl)}   ({pips(sl):.1f} pips)",
        f"🎯 TP1     {f(tp1)}   ({pips(tp1):.1f} pips, {r_of(tp1):.1f}R)",
        f"🎯 TP2     {f(tp2)}   ({pips(tp2):.1f} pips, {r_of(tp2):.1f}R)",
        "",
        "⏱ WHEN TO TAKE IT",
    ]
    if et == "market":
        late = entry + sgn * 0.5 * risk
        lines += [f"   Now, within about 15 minutes (until {_clock(close + pd.Timedelta(minutes=15), IST)} IST).",
                  f"   Skip it if price is already beyond {f(late)} (moved 0.5R without you)."]
    else:
        until = close + pd.Timedelta(minutes=max(expiry_bars, 1) * expiry_bar_min)
        lines += [f"   Place the order now. Valid until {_clock(until, IST, True)} IST.",
                  "   Cancel it if it is not filled by then, or if price closes beyond the SL first."]
    if status and not status.lower().startswith(et):
        lines.append(f"   Note: {status}")
    if size:
        lines += ["", f"💼 {size}"]
    if warn:
        lines += ["", warn]
    lines += [LINE, "Not financial advice. Use a stop-loss on every trade."]
    return "\n".join(lines)


def from_workflow(r: dict) -> str:
    sg = r["signal"]
    tp1, tp2 = sg["take_profit"]
    pos = sg.get("position") or {}
    size = (f"Size for {pos['risk_amount']} risk: about {pos['lots']} lots" + ("" if pos.get("exact") else " (approx., check your broker's calculator)")) if pos.get("lots") else ""
    plan_exec = sg["timeframe"]
    return card(symbol=sg["instrument"], side=sg["direction"], strategy=sg["strategy"],
                grade=grade_from_score(sg["confidence"]), score_text=f"score {sg['confidence']}/100",
                frames=plan_exec, ts=sg["timestamp"], bar_min=sg.get("exec_min", 5),
                entry=sg["entry"], entry_type=sg.get("entry_type", "market"), sl=sg["stop_loss"], tp1=tp1, tp2=tp2,
                dec=sg.get("dec", 5), pip=pip_size(sg["instrument"]), expiry_bars=sg.get("expiry", 0), expiry_bar_min=sg.get("setup_min", 15), size=size)


def from_analysis(an: dict) -> str:
    s = an["signal"]
    return card(symbol=an["symbol"], side=s["side"], strategy=s["strategy"], grade=s["grade"],
                score_text=f"{s['score']}/{s['of']} confirmations", frames=f"{an['mode'].title()} setup on the {an['interval']} chart",
                ts=an["time"], bar_min=BAR_MIN.get(an["interval"], 15), entry=s["entry"], entry_type=s["entry_type"],
                status=s["status"], sl=s["sl"], tp1=s["tp1"], tp2=s["tp2"], dec=an["price_dec"], pip=pip_size(an["symbol"]),
                expiry_bars=s.get("expiry", 0), expiry_bar_min=BAR_MIN.get(an["interval"], 15))
