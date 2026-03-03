"""
AX-SENSITIVITY-HYBRID-09: Hybrid sensitivity intelligence.
Optional sensitivity computation after main evaluation.
AX-LIVE-GRADIENT-SMOOTH-11: Exponential smoothing for LIVE wind gradient.
AX-SENSITIVITY-STATE-PURITY-13: Smoothing state owned by controller, not module.
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


def _wind_sensitivity_level(dP_dW: float) -> str:
    """Map gradient to High / Moderate / Low."""
    abs_g = abs(dP_dW)
    if abs_g >= 0.05:
        return "High"
    if abs_g >= 0.02:
        return "Moderate"
    return "Low"


def compute_sensitivity(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    mode: str,
    previous_wind_gradient: float | None = None,
    mc_call_counter: list | None = None,
) -> float | None:
    """
    AX-SENSITIVITY-HYBRID-09: Add sensitivity fields to snapshot (mutates in place).

    Args:
        snapshot: Base evaluation result (must have P_hit).
        config: Full config dict (wind_x, uav_altitude, uav_vx, n_samples, etc).
        mode: "standard" (LIVE-equivalent) or "advanced" (ANALYTICAL-equivalent)
        previous_wind_gradient: For standard mode, smoothed gradient from prior cycle.

    Returns:
        For standard: updated (smoothed) wind gradient.
        For advanced: None.
    """
    P_base = float(snapshot.get("P_hit", 0.0) or 0.0)

    if mode == "standard":
        # Wind +0.5 m/s ~ impacts shift ~2 m downwind; equivalent target shift (-2, 0)
        P_perturbed = _compute_p_hit_shift(snapshot, -2.0, 0.0)
        dP_dW = (P_perturbed - P_base) / 0.5 if 0.5 != 0 else 0.0

        # AX-LIVE-GRADIENT-SMOOTH-11: exponential smoothing
        alpha = 0.3
        if previous_wind_gradient is not None:
            g_smoothed = alpha * dP_dW + (1 - alpha) * previous_wind_gradient
        else:
            g_smoothed = dP_dW

        snapshot["sensitivity_live"] = {
            "wind_gradient_raw": dP_dW,
            "wind_gradient_smoothed": g_smoothed,
            "wind_gradient": g_smoothed,  # backward compat for consumers
            "wind_sensitivity": _wind_sensitivity_level(g_smoothed),
        }
        return g_smoothed

    if mode == "advanced":
        # Finite-difference via target shift: wind +0.5~(-2,0), altitude+5~(-5,0), velocity+2~(-4,0)
        P_w = _compute_p_hit_shift(snapshot, -2.0, 0.0)
        P_h = _compute_p_hit_shift(snapshot, -5.0, 0.0)
        P_v = _compute_p_hit_shift(snapshot, -4.0, 0.0)

        dP_dW = (P_w - P_base) / 0.5 if 0.5 != 0 else 0.0
        dP_dH = (P_h - P_base) / 5.0 if 5.0 != 0 else 0.0
        dP_dV = (P_v - P_base) / 2.0 if 2.0 != 0 else 0.0

        matrix = {
            "wind": dP_dW,
            "altitude": dP_dH,
            "velocity": dP_dV,
        }
        ranked = sorted(
            [("wind", abs(dP_dW)), ("altitude", abs(dP_dH)), ("velocity", abs(dP_dV))],
            key=lambda x: -x[1],
        )
        snapshot["sensitivity_matrix"] = matrix
        snapshot["dominant_risk_factor"] = ranked[0][0] if ranked else "wind"
        # Also set sensitivity_live for Control Center Current Factors display
        snapshot["sensitivity_live"] = {
            "wind_gradient_raw": dP_dW,
            "wind_gradient_smoothed": dP_dW,
            "wind_gradient": dP_dW,
            "wind_sensitivity": _wind_sensitivity_level(dP_dW),
        }
        return None
