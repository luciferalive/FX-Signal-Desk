"""Confluence engine: several strategies can support the same trade, but only INDEPENDENT ideas add up.

Two trend strategies agreeing is one vote (same family), not two. The result separates four stages:
    1 bias      what the higher timeframe allows
    2 setup     a strategy found a valid pattern
    3 trigger   the lower timeframe confirms entry
    4 risk      the trade passes the risk rules
and only then becomes BUY / SELL. WAIT and NO TRADE are normal, valid answers.
"""
from __future__ import annotations

from collections import defaultdict

FAMILY_FITS_REGIME = {
    "trend": {"trending"}, "breakout": {"trending", "transition"}, "swing": {"trending", "transition"},
    "mean_reversion": {"ranging"}, "price_action": {"trending", "ranging", "transition"},
    "scalping": {"trending", "transition", "ranging"}, "ict": {"trending", "transition", "ranging"},
}


def group(signals: list) -> dict:
    """{side: {family: [signals]}} (+1 / -1)."""
    out: dict = {1: defaultdict(list), -1: defaultdict(list)}
    for s in signals:
        out[s.side][s.family].append(s)
    return out


def score(side_groups: dict, regime: str, bias_ok: bool, in_killzone: bool) -> dict:
    """A heuristic 0-100 confluence score. It says how much independent evidence lines up, NOT the chance of winning.
        40 base for one valid setup
        +15 for each extra independent family on the same side (max 2)
        +15 if the strategy family suits the current regime
        +10 if the higher-timeframe bias agrees
        +10 if inside a killzone
    Opposing evidence (the other side also has setups) subtracts 15 per opposing family."""
    fams = list(side_groups)
    if not fams:
        return dict(score=0, families=[], notes=[])
    notes = [f"{len(fams)} independent idea(s): {', '.join(fams)}"]
    sc = 40 + 15 * min(len(fams) - 1, 2)
    if any(regime in FAMILY_FITS_REGIME.get(f, set()) for f in fams):
        sc += 15
        notes.append(f"suits the {regime} regime")
    else:
        notes.append(f"does not suit the {regime} regime")
    if bias_ok:
        sc += 10
        notes.append("agrees with the higher-timeframe bias")
    if in_killzone:
        sc += 10
        notes.append("inside a killzone")
    return dict(score=min(sc, 95), families=fams, notes=notes)


def combine(signals: list, regime: str, bias: str, in_killzone: bool) -> dict:
    """Pick the best side. Returns {side, signals, score, opposing, notes} or side 0 when nothing qualifies."""
    g = group(signals)
    best = None
    for side in (1, -1):
        if not g[side]:
            continue
        bias_ok = (bias == "BUY" and side == 1) or (bias == "SELL" and side == -1)
        sc = score(g[side], regime, bias_ok, in_killzone)
        sc["score"] = max(0, sc["score"] - 15 * len(g[-side]))
        if sc["score"] and (best is None or sc["score"] > best["score"]):
            best = dict(side=side, score=sc["score"], notes=sc["notes"], families=sc["families"],
                        signals=[s for fam in g[side].values() for s in fam], opposing=sorted(g[-side]))
    return best or dict(side=0, score=0, notes=[], families=[], signals=[], opposing=[])
