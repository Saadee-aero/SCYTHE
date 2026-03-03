"""Thin engine adapter for Qt UI snapshot simulation calls."""

from __future__ import annotations

from pathlib import Path
import sys
import numpy as np
from datetime import datetime
from typing import Any, Dict


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _compute_prob_curves(
    snapshot: Dict[str, Any],
    n_points: int = 8,
) -> tuple[tuple[list, list], tuple[list, list]]:
    """
    Compute P(hit) vs target_radius and P(hit) vs target offset (wind-uncertainty proxy).
    Uses snapshot impact_points only. No Monte Carlo or propagation.
    Returns ((x_dist, y_dist), (x_wind, y_wind)).
    """
    impact_points = np.asarray(snapshot["impact_points"], dtype=float)
    target = np.asarray(snapshot["target_position"], dtype=float).flatten()[:2]
    base_radius = float(snapshot["target_radius"])

    if impact_points.size == 0 or impact_points.ndim != 2 or impact_points.shape[1] < 2:
        r_min, r_max = 1.0, max(15.0, base_radius * 2)
        radii = np.linspace(r_min, r_max, n_points)
        x_dist = radii.tolist()
        y_dist = [0.0] * n_points
        x_wind = np.linspace(0.2, 2.5, n_points).tolist()
        y_wind = [0.0] * n_points
        return ((x_dist, y_dist), (x_wind, y_wind))

    impacts_2d = impact_points[:, :2]
    distances_to_target = np.linalg.norm(impacts_2d - target, axis=1)

    # Curve 1: P(hit) vs target radius — vary radius, same impacts
    r_min, r_max = 1.0, max(15.0, base_radius * 2)
    radii = np.linspace(r_min, r_max, n_points)
    hit_matrix = distances_to_target[:, None] <= radii[None, :]
    p_curve_radius = np.mean(hit_matrix, axis=0)
    x_dist = radii.tolist()
    y_dist = p_curve_radius.tolist()

    # Curve 2: P(hit) vs wind_std (approximate via target shift proxy)
    # Map wind_std to effective displacement: shift = (ws - base_ws) * 3
    base_wind_std = float(
        snapshot.get("telemetry", {}).get("wind_std", 1.0)
    )
    x_wind = np.linspace(0.2, 2.5, n_points)
    shifts = (x_wind - base_wind_std) * 3.0
    shifted_targets = target[None, :] + np.stack([shifts, np.zeros_like(shifts)], axis=1)
    dists = np.linalg.norm(
        impacts_2d[None, :, :] - shifted_targets[:, None, :],
        axis=2,
    )
    p_curve_shift = np.mean(dists <= base_radius, axis=1)
    x_wind = x_wind.tolist()
    y_wind = p_curve_shift.tolist()

    return ((x_dist, y_dist), (x_wind, y_wind))


def run_simulation_snapshot(
    config_override: Dict[str, Any] | None = None,
    include_advisory: bool = False,
    previous_wind_gradient: float | None = None,
    *,
    caller: str = "BASE",
    trace_mode: str | None = None,
    mc_call_counter: list | None = None,
) -> Dict[str, Any]:
    """Run one simulation snapshot using existing engine pipeline. AX-MC-CALL-TRACE-25."""
    from configs import mission_configs as cfg
    from product.payloads.payload_base import Payload
    from product.missions.target_manager import Target
    from product.missions.environment import Environment
    from product.missions.mission_state import MissionState
    from product.guidance.advisory_layer import (
        evaluate_advisory,
        enrich_snapshot_with_opportunity_explorer,
        get_impact_points_and_metrics,
    )

    overrides = dict(config_override or {})
    is_outer = mc_call_counter is None
    if mc_call_counter is None:
        mc_call_counter = [0]
    # Engine entry observability
    print("DISPLAY =", overrides.get("display_mode"))
    print("FIDELITY =", overrides.get("simulation_fidelity"))
    print("EXECUTION =", overrides.get("execution_mode"))
    mode = trace_mode if trace_mode is not None else str(overrides.get("simulation_fidelity", "advanced")).strip().lower() or "advanced"
    print(f"[ENGINE TRACE] mode={mode}")

    mass = float(overrides.get("mass", cfg.mass))
    cd = float(overrides.get("cd", cfg.Cd))
    area = float(overrides.get("area", cfg.A))

    uav_pos = (
        float(overrides.get("uav_x", cfg.uav_pos[0])),
        float(overrides.get("uav_y", cfg.uav_pos[1])),
        float(overrides.get("uav_altitude", cfg.uav_pos[2])),
    )
    uav_vel = (
        float(overrides.get("uav_vx", cfg.uav_vel[0])),
        float(overrides.get("uav_vy", cfg.uav_vel[1])),
        float(overrides.get("uav_vz", cfg.uav_vel[2])),
    )
    target_pos = (
        float(overrides.get("target_x", cfg.target_pos[0])),
        float(overrides.get("target_y", cfg.target_pos[1])),
        float(overrides.get("target_elevation", cfg.target_pos[2] if len(cfg.target_pos) >= 3 else 0.0)),
    )
    target_radius = float(overrides.get("target_radius", cfg.target_radius))
    wind_mean = (
        float(overrides.get("wind_x", overrides.get("wind_mean_x", cfg.wind_mean[0]))),
        float(overrides.get("wind_y", overrides.get("wind_mean_y", cfg.wind_mean[1]))),
        float(cfg.wind_mean[2] if len(cfg.wind_mean) > 2 else 0.0),
    )
    wind_std = float(overrides.get("wind_std", cfg.wind_std))
    overrides.setdefault("n_samples", cfg.n_samples)
    _raw_seed = overrides.get("random_seed", cfg.RANDOM_SEED)
    random_seed = 42 if _raw_seed is None else int(_raw_seed)
    threshold_pct = float(overrides.get("threshold_pct", cfg.THRESHOLD_SLIDER_INIT))

    payload = Payload(
        mass=mass,
        drag_coefficient=cd,
        reference_area=area,
    )
    target = Target(position=target_pos, radius=target_radius)
    environment = Environment(wind_mean=wind_mean, wind_std=wind_std)
    mission_state = MissionState(
        payload=payload,
        target=target,
        environment=environment,
        uav_position=uav_pos,
        uav_velocity=uav_vel,
    )

    mc_call_counter[0] += 1
    impact_points, P_hit, cep50, impact_velocity_stats = get_impact_points_and_metrics(
        mission_state, random_seed, overrides, caller=caller, mode=mode
    )

    advisory_result = None
    if include_advisory:
        base_snapshot = {
            "impact_points": impact_points,
            "P_hit": P_hit,
            "cep50": cep50,
            "target_position": mission_state.target.position,
            "target_radius": mission_state.target.radius,
        }
        advisory_result = evaluate_advisory(
            base_snapshot,
            threshold_pct / 100.0,
            trace_mode=mode,
        )

    # Confidence index for Mission Overview banner
    from src import metrics
    bc = (mass / (cd * area)) if (cd and area) else None
    confidence_index = metrics.compute_confidence_index(
        wind_std=wind_std,
        ballistic_coefficient=bc,
        altitude=uav_pos[2],
        telemetry_freshness=None,
    )

    # True integer hit count (same logic as metrics.compute_hit_probability)
    import numpy as np
    impact_arr = np.asarray(impact_points, dtype=float)
    if impact_arr.size > 0 and impact_arr.ndim == 2 and impact_arr.shape[1] >= 2:
        target_2d = np.asarray(mission_state.target.position, dtype=float).flatten()[:2]
        radial_distances = np.linalg.norm(impact_arr[:, :2] - target_2d, axis=1)
        hits = int(np.sum(radial_distances <= mission_state.target.radius))
        n_actual = int(impact_arr.shape[0])
        P_hit = float(hits) / float(n_actual) if n_actual > 0 else 0.0
    else:
        hits = 0
        n_actual = n_samples
        P_hit = 0.0

    # Telemetry-like dict for unified Control Center rendering (SNAPSHOT path)
    telemetry = {
        "x": uav_pos[0],
        "y": uav_pos[1],
        "z": uav_pos[2],
        "vx": uav_vel[0],
        "vy": uav_vel[1],
        "wind_x": wind_mean[0],
        "wind_y": wind_mean[1] if len(wind_mean) > 1 else 0.0,
        "wind_std": wind_std,
    }
    # Wilson CI and doctrine for SNAPSHOT path (uses true integer hits)
    from src.statistics import compute_wilson_ci
    from src.decision_doctrine import evaluate_doctrine, DOCTRINE_DESCRIPTIONS
    ci_low, ci_high = compute_wilson_ci(hits, n_actual)
    doctrine = str(overrides.get("doctrine_mode", "BALANCED")).strip().upper()
    doctrine_result = evaluate_doctrine(
        p_hat=P_hit,
        ci_low=ci_low,
        ci_high=ci_high,
        threshold=threshold_pct / 100.0,
        doctrine=doctrine,
        n_samples=n_actual,
    )
    result = {
        "impact_points": impact_points,
        "hits": hits,
        "P_hit": P_hit,
        "cep50": cep50,
        "target_position": mission_state.target.position,
        "target_radius": mission_state.target.radius,
        "advisory": advisory_result,
        "wind_vector": tuple(wind_mean[:2]),
        "impact_velocity_stats": impact_velocity_stats,
        "snapshot_id": datetime.now().strftime("AX-%Y%m%d-%H%M%S"),
        "confidence_index": confidence_index,
        "telemetry": telemetry,
        "n_samples": n_actual,
        "random_seed": random_seed,
        "threshold_pct": threshold_pct,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_hat": P_hit,
        "decision": doctrine_result["decision"],
        "decision_reason": doctrine_result["reason"],
        "doctrine_mode": doctrine,
        "doctrine_description": doctrine_result.get("doctrine_description") or DOCTRINE_DESCRIPTIONS.get(doctrine, doctrine),
    }

    payload_config = {"mass": mass, "cd": cd, "area": area}
    enrich_snapshot_with_opportunity_explorer(
        result,
        uav_pos,
        uav_vel,
        payload_config,
        wind_mean,
        shear=None,
        target_position=mission_state.target.position,
        target_radius=mission_state.target.radius,
    )

    # AX-SENSITIVITY-HYBRID-09: optional sensitivity computation
    simulation_fidelity = str(overrides.get("simulation_fidelity", "")).strip().lower()
    if simulation_fidelity in ("standard", "advanced"):
        try:
            from src.sensitivity import compute_sensitivity

            updated_gradient = compute_sensitivity(
                result, overrides, simulation_fidelity,
                previous_wind_gradient=previous_wind_gradient,
                mc_call_counter=mc_call_counter,
            )
            if updated_gradient is not None:
                result["updated_wind_gradient"] = updated_gradient
        except Exception:
            pass  # Non-fatal; snapshot remains valid

    # AX-MISS-TOPOLOGY-HYBRID-12: topology layer (after sensitivity, before emission)
    if simulation_fidelity in ("standard", "advanced"):
        try:
            from src.topology import compute_topology

            compute_topology(result, simulation_fidelity)
        except Exception:
            pass  # Non-fatal; snapshot remains valid

    # AX-RELEASE-CORRIDOR-19: release corridor (after topology)
    if simulation_fidelity in ("standard", "advanced"):
        try:
            from src.release_corridor import compute_release_corridor

            compute_release_corridor(result, overrides, simulation_fidelity, mc_call_counter=mc_call_counter)
        except Exception:
            pass  # Non-fatal; snapshot remains valid

    # AX-FRAGILITY-SURFACE-20: fragility state (uses sensitivity when advanced fidelity)
    if simulation_fidelity in ("standard", "advanced"):
        try:
            from src.fragility import compute_fragility

            compute_fragility(result, overrides, simulation_fidelity, mc_call_counter=mc_call_counter)
        except Exception:
            pass  # Non-fatal; snapshot remains valid

    # AX-UNCERTAINTY-DECOMPOSITION-21 / Phase 2: true conditional MC variance decomposition
    if simulation_fidelity == "advanced":
        try:
            from product.analysis.variance_decomposition import compute_uncertainty_contributions

            uc = compute_uncertainty_contributions(
                result, overrides, N=500, mc_call_counter=mc_call_counter,
            )
            if uc is not None:
                result["uncertainty_contribution"] = uc
        except Exception:
            pass  # Non-fatal; snapshot remains valid

    # Prob curves for Analysis tab graphs
    if simulation_fidelity in ("standard", "advanced") and is_outer:
        try:
            (result["prob_vs_distance"], result["prob_vs_wind_uncertainty"]) = _compute_prob_curves(
                result, n_points=8
            )
        except Exception:
            result.setdefault("prob_vs_distance", None)
            result.setdefault("prob_vs_wind_uncertainty", None)

    if is_outer:
        print(f"[MC SUMMARY] total_calls_this_cycle={mc_call_counter[0]}")

    # EVALUATION snapshot invariant: required statistical fields must be present
    _required = ("n_samples", "P_hit", "ci_low", "ci_high", "threshold_pct", "decision")
    for key in _required:
        if key not in result:
            raise ValueError(f"Evaluation snapshot missing required field: {key!r}")
    if not isinstance(result["n_samples"], int):
        raise ValueError("Evaluation snapshot n_samples must be int")
    if not isinstance(result["P_hit"], (int, float)):
        raise ValueError("Evaluation snapshot P_hit must be float")
    if not isinstance(result["ci_low"], (int, float)):
        raise ValueError("Evaluation snapshot ci_low must be float")
    if not isinstance(result["ci_high"], (int, float)):
        raise ValueError("Evaluation snapshot ci_high must be float")
    print("[SNAPSHOT] Evaluation snapshot validated")
    return result
