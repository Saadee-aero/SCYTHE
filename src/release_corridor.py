"""
AX-RELEASE-CORRIDOR-19: Release corridor intelligence.
Region along longitudinal axis where P_hit >= threshold.
AX-RELEASE-CORRIDOR-HARDEN-22: Tolerance and resolution safeguard.
"""

from __future__ import annotations

import numpy as np
from typing import Any


def _compute_p_hit_shift(snapshot: dict, dx: float, dy: float) -> float:
    """P_hit for target shifted by (dx, dy). Pure NumPy, no propagation."""
    tp = np.asarray(snapshot["target_position"], dtype=float).flatten()[:2]
    shifted_target = tp + np.array([dx, dy], dtype=float)
    impact_pts = np.asarray(snapshot["impact_points"], dtype=float)
    if impact_pts.size == 0 or impact_pts.ndim != 2 or impact_pts.shape[1] < 2:
        return 0.0
    distances = np.linalg.norm(impact_pts[:, :2] - shifted_target, axis=1)
    return float(np.mean(distances <= snapshot["target_radius"]))


def compute_release_corridor(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    mode: str,
    mc_call_counter: list | None = None,
) -> None:
    """
    AX-RELEASE-CORRIDOR-19: Add release corridor fields to snapshot (mutates in place).

    Args:
        snapshot: Evaluation result (must have P_hit, threshold_pct).
        config: Full config dict (uav_x, n_samples, etc).
        mode: "standard" (LIVE-equivalent) or "advanced" (ANALYTICAL-equivalent)
    """
    P_base = float(snapshot.get("P_hit", 0.0) or 0.0)
    threshold_pct = float(snapshot.get("threshold_pct", 75.0) or 75.0)
    threshold = threshold_pct / 100.0
    epsilon_pct = 0.5
    threshold_eff = threshold - (epsilon_pct / 100.0)
    dx = 1.0

    if mode == "standard":
        # UAV offset ±dx ~ target shift ∓dx
        P_minus = _compute_p_hit_shift(snapshot, dx, 0.0)
        P_plus = _compute_p_hit_shift(snapshot, -dx, 0.0)

        both_ok = (P_minus >= threshold_eff) and (P_plus >= threshold_eff)
        one_ok = (P_minus >= threshold_eff) or (P_plus >= threshold_eff)

        if both_ok:
            corridor_width_m = 2.0 * dx
        elif one_ok:
            corridor_width_m = dx
        else:
            corridor_width_m = 0.0

        margin_pct = (P_base - threshold) * 100.0
        snapshot["release_corridor_live"] = {
            "corridor_width_m": corridor_width_m,
            "margin_pct": margin_pct,
        }
        return

    if mode == "advanced":
        # Scan dx offsets: UAV +off ~ target shift -off
        offsets = list(range(-5, 6))
        p_hits = []
        for off in offsets:
            p = _compute_p_hit_shift(snapshot, -float(off), 0.0)
            p_hits.append((off, p))

        in_corridor = [(o, p) for o, p in p_hits if p >= threshold_eff]
        step_size = 1.0
        if not in_corridor:
            min_offset_m = 0.0
            max_offset_m = 0.0
            corridor_width_m = 0.0
        else:
            offsets_in = [o for o, _ in in_corridor]
            min_offset_m = float(min(offsets_in))
            max_offset_m = float(max(offsets_in))
            corridor_width_m = max_offset_m - min_offset_m
            if corridor_width_m > 0 and corridor_width_m < step_size:
                corridor_width_m = "<1.0"

        snapshot["release_corridor_matrix"] = {
            "min_offset_m": min_offset_m,
            "max_offset_m": max_offset_m,
            "corridor_width_m": corridor_width_m,
        }
        # Also set release_corridor_live for Control Center Current Factors display
        margin_pct = (P_base - threshold) * 100.0
        snapshot["release_corridor_live"] = {
            "corridor_width_m": corridor_width_m,  # may be float or "<1.0"
            "margin_pct": margin_pct,
        }
        return
