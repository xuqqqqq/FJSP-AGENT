from __future__ import annotations

import math


ZI_SLOT_MAX_ABS = 1.0e9


# EVOLVE_START
def evolved_zi(values: dict[str, float]) -> float:
    """Return the AWLS zi perturbation for one operation.

    This function is the only source-code slot that the guarded slot worker is
    allowed to rewrite.  The surrounding solver, parser, evaluator, and
    benchmark harness remain fixed so that code evolution is auditable.
    """

    weight = float(values.get("weight", 0.0))
    cooldown = float(values.get("cooldown", 0.0))
    rr = max(1.0e-9, float(values.get("rr", 1.0)))
    cooling = max(0.0, 1.0 - cooldown / rr)
    return max(0.0, cooling * weight)
# EVOLVE_END


def safe_evolved_zi(values: dict[str, float]) -> float:
    """Execute the evolved zi slot with numeric guardrails."""

    try:
        value = float(evolved_zi(dict(values)))
    except Exception:
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return min(ZI_SLOT_MAX_ABS, max(0.0, value))
