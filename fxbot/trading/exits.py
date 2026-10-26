"""Exit planning: how many targets a trade has and exactly how much volume each one closes, within broker limits."""
from __future__ import annotations

from .models import SymbolSpec
from .sizing import floor_to_step


def plan_exits(volume: float, n_targets: int, fractions: list[float], spec: SymbolSpec) -> list[float | None]:
    """Volume to close at each target. Fractions are shares of the ORIGINAL volume for every target except the last,
    which always closes whatever is left (None). Rules that keep every request valid:
      * a partial is rounded DOWN to the broker step;
      * a partial smaller than the broker minimum is skipped (0.0), never rounded up;
      * a partial that would leave less than the broker minimum behind is reduced so the remainder stays valid, or
        skipped if that is impossible. A skipped partial just means that target does nothing;
      * the whole position is only ever closed by the last target (or a stop / manual action).
    A small account can therefore end up with a single target. That is intended: it is the safe outcome."""
    n = max(1, n_targets)
    plan: list[float | None] = []
    left = volume
    for i in range(n - 1):
        want = fractions[i] if i < len(fractions) else 0.0
        v = floor_to_step(volume * want, spec.volume_step)
        if left - v < spec.volume_min - 1e-12:
            v = floor_to_step(left - spec.volume_min, spec.volume_step)   # keep a valid remainder
        if v < spec.volume_min - 1e-12:
            plan.append(0.0)
            continue
        plan.append(round(v, 8))
        left = round(left - v, 8)
    plan.append(None)
    return plan
