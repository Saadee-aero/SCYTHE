"""
Decision doctrine evaluator for AIRDROP-X.
Operator-selectable statistical doctrine for drop decision.
"""
from __future__ import annotations

from typing import Any

DOCTRINE_DESCRIPTIONS: dict[str, str] = {
    "STRICT": "Drop only if lower confidence bound exceeds threshold.",
    "BALANCED": "Drop based on estimated hit probability.",
    "AGGRESSIVE": "Drop if upper confidence bound exceeds threshold.",
}

# Minimum samples required for statistical validity. Adaptive sampling must not go below this.
MIN_VALID_N = 30


def evaluate_doctrine(
    p_hat: float,
    ci_low: float,
    ci_high: float,
    threshold: float,
    doctrine: str,
    n_samples: int,
) -> dict[str, Any]:
    """
    Evaluate drop decision using doctrine-based rules.

    Args:
        p_hat: Estimated hit probability.
        ci_low: Lower Wilson CI bound (0–1).
        ci_high: Upper Wilson CI bound (0–1).
        threshold: Threshold fraction (0–1, e.g. 0.75 for 75%).
        doctrine: STRICT | BALANCED | AGGRESSIVE.
        n_samples: Number of Monte Carlo samples.

    Returns:
        {"decision": "DROP"|"NO DROP", "reason": str, "doctrine_description": str}
    """
    # N guard
    if n_samples < MIN_VALID_N:
        return {
            "decision": "NO DROP",
            "reason": "Insufficient statistical sample size.",
            "doctrine_description": DOCTRINE_DESCRIPTIONS.get(doctrine, doctrine),
        }

    doctrine_upper = str(doctrine).strip().upper()
    desc = DOCTRINE_DESCRIPTIONS.get(doctrine_upper)

    if doctrine_upper == "STRICT":
        drop = ci_low >= threshold
        reason = "Lower CI bound exceeds threshold." if drop else "Lower CI bound below threshold."
    elif doctrine_upper == "BALANCED":
        drop = p_hat >= threshold
        reason = "Estimated hit probability exceeds threshold." if drop else "Estimated hit probability below threshold."
    elif doctrine_upper == "AGGRESSIVE":
        drop = ci_high >= threshold
        reason = "Upper CI bound exceeds threshold." if drop else "Upper CI bound below threshold."
    else:
        raise ValueError(f"Unknown doctrine: {doctrine}")

    return {
        "decision": "DROP" if drop else "NO DROP",
        "reason": reason,
        "doctrine_description": desc or doctrine_upper,
    }
