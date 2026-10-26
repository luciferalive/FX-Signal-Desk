"""Trade arbitration: many strategies may fire at once, but that must never mean many trades.

  1. exact duplicates are dropped;
  2. BUY and SELL on the same symbol conflict: rejected by default, or settled by an explicit strategy priority;
  3. same-direction signals at nearly the same price are consolidated into ONE proposal (the highest-priority
     strategy supplies entry, stop and targets, the others are recorded as supporting evidence).
The result still goes through the risk manager. Every decision comes back with a plain-words explanation."""
from __future__ import annotations

from dataclasses import replace

from .risk_manager import Proposal


def _rank(p: Proposal, priority: list[str]) -> tuple[int, float]:
    try:
        i = priority.index(p.strategy)
    except ValueError:
        i = len(priority)
    return (i, -p.confidence)


def arbitrate(candidates: list[Proposal], cfg: dict) -> tuple[list[Proposal], list[dict]]:
    a = cfg["arbitration"]
    notes: list[dict] = []
    seen, uniq = set(), []
    for c in candidates:
        if c.signal_id in seen:
            notes.append(dict(symbol=c.symbol, strategy=c.strategy, action="dropped", why="duplicate signal"))
            continue
        seen.add(c.signal_id)
        uniq.append(c)

    out: list[Proposal] = []
    for sym in dict.fromkeys(c.symbol for c in uniq):
        group = [c for c in uniq if c.symbol == sym]
        buys = [c for c in group if c.side > 0]
        sells = [c for c in group if c.side < 0]
        if buys and sells:
            if a["conflict"] == "priority":
                best_buy, best_sell = min(buys, key=lambda c: _rank(c, a["priority"])), min(sells, key=lambda c: _rank(c, a["priority"]))
                winner = best_buy if _rank(best_buy, a["priority"]) <= _rank(best_sell, a["priority"]) else best_sell
                loser_side = -winner.side
                notes.append(dict(symbol=sym, strategy=winner.strategy, action="conflict_resolved",
                                  why=f"BUY and SELL signals conflict; {winner.strategy} has the higher configured priority, "
                                      f"the {'SELL' if loser_side < 0 else 'BUY'} side is ignored"))
                group = [c for c in group if c.side == winner.side]
            else:
                names = ", ".join(sorted({c.strategy for c in group}))
                notes.append(dict(symbol=sym, strategy=names, action="rejected",
                                  why=f"conflicting BUY and SELL signals ({names}); conflicts are rejected by default"))
                continue
        for side in (1, -1):
            side_group = sorted([c for c in group if c.side == side], key=lambda c: _rank(c, a["priority"]))
            clusters: list[list[Proposal]] = []
            for c in side_group:
                tol = a["price_tolerance_atr"] * (c.atr or abs(c.entry - c.sl))
                for cl in clusters:
                    if abs(cl[0].entry - c.entry) <= tol:
                        cl.append(c)
                        break
                else:
                    clusters.append([c])
            for cl in clusters:
                base = cl[0]
                if len(cl) == 1:
                    out.append(replace(base, sources=base.sources or [base.strategy]))
                    continue
                srcs = list(dict.fromkeys(s for m in cl for s in (m.sources or [m.strategy])))
                expl = list(dict.fromkeys(e for m in cl for e in m.explanation))
                expl.append(f"consolidated {len(cl)} signals at nearly the same price into one trade: {', '.join(srcs)}")
                out.append(replace(base, explanation=expl, sources=srcs, confidence=max(m.confidence for m in cl)))
                notes.append(dict(symbol=sym, strategy=base.strategy, action="consolidated",
                                  why=f"{len(cl)} {'BUY' if side > 0 else 'SELL'} signals ({', '.join(srcs)}) within "
                                      f"{a['price_tolerance_atr']} ATR became one proposal led by {base.strategy}"))
    return out, notes
