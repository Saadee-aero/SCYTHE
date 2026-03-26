"""
Decision hysteresis, robustness qualification, and stability index.
AX-DECISION-STABILITY-01.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HYSTERESIS_MARGIN_PCT = 1.0
EPSILON = 1e-6  # AX-STABILITY-CLAMP-02: min ci_width for stability_index


def apply_decision_hysteresis(
    raw_decision: str,
    p_hit: float,
    threshold_pct: float,
    previous_decision: str | None,
) -> str:
    """
    Apply hysteresis to decision.
    threshold_pct is 0-100; p_hit is 0-1. Convert for comparison.
    """
    th_frac = threshold_pct / 100.0
    hyst_frac = HYSTERESIS_MARGIN_PCT / 100.0
    prev = (previous_decision or "").strip().upper()

    if prev == "DROP":
        if p_hit >= th_frac - hyst_frac:
            return "DROP"
    elif prev == "NO DROP":
        if p_hit <= th_frac + hyst_frac:
            return "NO DROP"

    # No previous or outside hysteresis band: use raw
    return raw_decision if raw_decision in ("DROP", "NO DROP") else (
        "DROP" if p_hit >= th_frac else "NO DROP"
    )


def compute_robustness_status(
    ci_low: float | None,
    ci_high: float | None,
    threshold_pct: float,
) -> str:
    """ci_low, ci_high in [0,1]; threshold_pct in 0-100."""
    if ci_low is None or ci_high is None:
        return "UNKNOWN"
    th = threshold_pct / 100.0
    if ci_low > th:
        return "ROBUST"
    if ci_high < th:
        return "UNSAFE"
    return "FRAGILE"


def compute_stability_index(
    p_hit: float,
    threshold_pct: float,
    ci_low: float | None,
    ci_high: float | None,
) -> float:
    """Higher = more stable (further from threshold relative to CI width). AX-STABILITY-CLAMP-02."""
    th = threshold_pct / 100.0
    dist = abs(p_hit - th)
    ci_width = 0.0
    if ci_low is not None and ci_high is not None and ci_high > ci_low:
        ci_width = ci_high - ci_low
    denom = max(ci_width, EPSILON)
    si = dist / denom
    return min(si, 100.0)


def enrich_evaluation_snapshot(
    snapshot: dict,
    previous_decision: str | None,
) -> dict:
    """
    Add robustness_status, stability_index, apply hysteresis to decision.
    Modifies snapshot in place and returns it.
    """
    p_hit = float(snapshot.get("P_hit", 0.0) or 0.0)
    th = float(snapshot.get("threshold_pct", 75.0))
    ci_low = snapshot.get("ci_low")
    ci_high = snapshot.get("ci_high")
    raw = str(snapshot.get("decision", "NO DROP")).strip().upper()
    if raw not in ("DROP", "NO DROP"):
        raw = "DROP" if (p_hit * 100.0) >= th else "NO DROP"

    logger.debug("Hysteresis applied: previous_decision=%s", previous_decision)
    final = apply_decision_hysteresis(raw, p_hit, th, previous_decision)
    snapshot["decision"] = final

    ci_width = 0.0
    if ci_low is not None and ci_high is not None and ci_high > ci_low:
        ci_width = ci_high - ci_low
    if ci_width < EPSILON:
        snapshot["robustness_status"] = "NUMERICAL_LIMIT"
    else:
        snapshot["robustness_status"] = compute_robustness_status(ci_low, ci_high, th)
    snapshot["stability_index"] = compute_stability_index(p_hit, th, ci_low, ci_high)
    return snapshot
