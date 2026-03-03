"""
AX-MISS-TOPOLOGY-HYBRID-12: Hybrid miss topology intelligence.
Extends snapshot with drift and dispersion classification.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _classify_drift_axis(mean_x: float, mean_y: float) -> str:
    """Classify primary drift axis from mean offset."""
    ax = abs(mean_x)
    ay = abs(mean_y)
    if ax > ay:
        return "longitudinal"
    if ay > ax:
        return "lateral"
    return "centered"


def _classify_dispersion(eccentricity_ratio: float) -> str:
    """Classify dispersion shape from eccentricity ratio."""
    if eccentricity_ratio < 1.2:
        return "Circular"
    if eccentricity_ratio < 2.0:
        return "Moderate elongation"
    return "Strong elongation"


def compute_topology(snapshot: dict[str, Any], mode: str) -> None:
    """
    AX-MISS-TOPOLOGY-HYBRID-12: Add topology fields to snapshot (mutates in place).

    Args:
        snapshot: Evaluation result (must have impact_points).
        mode: "standard" (LIVE-equivalent) or "advanced" (ANALYTICAL-equivalent)
    """
    impact_points = snapshot.get("impact_points")
    if impact_points is None or len(impact_points) == 0:
        return

    pts = np.asarray(impact_points, dtype=float)
    if pts.ndim == 1:
        pts = pts.reshape(-1, 2)
    if pts.shape[0] < 2 or pts.shape[1] < 2:
        return

    if mode == "standard":
        mean_x = float(np.mean(pts[:, 0]))
        mean_y = float(np.mean(pts[:, 1]))
        var_x = float(np.var(pts[:, 0]))
        var_y = float(np.var(pts[:, 1]))
        drift_axis = _classify_drift_axis(mean_x, mean_y)
        snapshot["topology_live"] = {
            "mean_x": mean_x,
            "mean_y": mean_y,
            "var_x": var_x,
            "var_y": var_y,
            "drift_axis": drift_axis,
        }
        return

    if mode == "advanced":
        mean_vec = np.mean(pts, axis=0)
        cov = np.cov(pts.T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        order = eigvals.argsort()[::-1]
        eigvals = eigvals[order]
        eigvecs = eigvecs[:, order]
        lambda_max = max(float(eigvals[0]), 1e-12)
        lambda_min = max(float(eigvals[1]), 1e-12)
        eccentricity_ratio = np.sqrt(lambda_max / lambda_min)
        ev = eigvecs[:, 0]
        principal_angle_deg = float(np.degrees(np.arctan2(ev[1], ev[0])))
        classification = _classify_dispersion(eccentricity_ratio)
        snapshot["topology_matrix"] = {
            "mean_vector": mean_vec.tolist(),
            "covariance_matrix": cov.tolist(),
            "eigenvalues": eigvals.tolist(),
            "eigenvectors": eigvecs.tolist(),
            "principal_axis_angle_deg": principal_angle_deg,
            "eccentricity_ratio": eccentricity_ratio,
            "dispersion_classification": classification,
        }
        # Also set topology_live for Control Center Current Factors display
        mean_x, mean_y = float(mean_vec[0]), float(mean_vec[1])
        drift_axis = _classify_drift_axis(mean_x, mean_y)
        snapshot["topology_live"] = {
            "mean_x": mean_x,
            "mean_y": mean_y,
            "var_x": float(np.var(pts[:, 0])),
            "var_y": float(np.var(pts[:, 1])),
            "drift_axis": drift_axis,
        }
        return
