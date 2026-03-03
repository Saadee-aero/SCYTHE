"""
Advisory guidance only. No waypoints, no control commands, no optimization.
Uses the engine as a black box to evaluate drop feasibility.

Single source of PropagationContext creation for deterministic and Monte Carlo paths.
"""

import numpy as np

from product.physics.propagation_context import build_propagation_context

EXPLORER_STATUS_DEFAULT = "NOT_INVOKED"
EXPLORER_STATUS_FEASIBLE = "FEASIBLE_SHIFT_FOUND"
EXPLORER_STATUS_OUT_OF_RANGE = "OUT_OF_RANGE"

# Position step for nearby feasibility checks (m). SI.
ADVISORY_POSITION_DELTA_M = 5.0

# Keys we temporarily set on config when running the engine with MissionState inputs.
_ENGINE_INPUT_KEYS = (
    "uav_pos",
    "uav_vel",
    "target_pos",
    "target_radius",
    "wind_mean",
    "wind_std",
    "mass",
    "A",
    "Cd",
)


def _make_context(mass, Cd, area, wind_ref, shear, target_z, dt):
    """Build context via propagation_context factory."""
    wind_ref = np.asarray(wind_ref, dtype=float).reshape(3)
    shear_arr = np.zeros(3, dtype=float) if shear is None else np.asarray(shear, dtype=float).reshape(3)
    return build_propagation_context(
        mass=float(mass),
        Cd=float(Cd),
        area=float(area),
        wind_ref=wind_ref,
        shear=shear_arr,
        target_z=float(target_z),
        dt=float(dt),
    )


def get_impact_points_and_metrics(
    mission_state,
    random_seed,
    config,
    *,
    caller: str = "BASE",
    mode: str = "advanced",
):
    """
    Run engine once for the given mission state; return impact points and metrics.
    For use by product entry point (e.g. UI).
    Returns (impact_points, P_hit, cep50, impact_velocity_stats).
    impact_velocity_stats: dict with mean_impact_speed, std_impact_speed, p95_impact_speed (m/s).
    AX-MC-CALL-TRACE-25: caller and mode passed through to MC trace.
    """
    from configs import mission_configs as cfg
    from src import monte_carlo
    from src import metrics

    mission_state.validate()
    engine_inputs = mission_state.export_engine_inputs()
    saved = {}
    try:
        for key in _ENGINE_INPUT_KEYS:
            saved[key] = getattr(cfg, key)
        for key, value in engine_inputs.items():
            setattr(cfg, key, value)
        target_pos = engine_inputs["target_pos"]
        target_z = float(target_pos[2]) if len(target_pos) >= 3 else 0.0
        context = _make_context(
            mass=cfg.mass,
            Cd=cfg.Cd,
            area=cfg.A,
            wind_ref=cfg.wind_mean,
            shear=getattr(cfg, "shear", None),
            target_z=target_z,
            dt=cfg.dt,
        )
        impact_points, impact_speeds = monte_carlo.run_monte_carlo(
            context,
            cfg.uav_pos,
            cfg.uav_vel,
            cfg.wind_std,
            config,
            random_seed,
            return_impact_speeds=True,
            caller=caller,
            mode=mode,
        )
        target_radius = engine_inputs["target_radius"]
        P_hit = metrics.compute_hit_probability(
            impact_points, target_pos, target_radius
        )
        cep50 = metrics.compute_cep50(impact_points, target_pos)
        impact_velocity_stats = metrics.compute_impact_velocity_stats(impact_speeds)
        return (impact_points, P_hit, cep50, impact_velocity_stats)
    finally:
        for key, value in saved.items():
            setattr(cfg, key, value)


def enrich_snapshot_with_opportunity_explorer(
    snapshot: dict,
    uav_pos,
    uav_vel,
    payload_config: dict,
    wind_ref,
    shear,
    target_position,
    target_radius: float,
) -> None:
    """
    When decision == "NO DROP", run 1D opportunity explorer and attach results.
    Mutates snapshot in place. Always sets snapshot["explorer_status"].
    """
    from product.guidance.opportunity_explorer import find_release_shift_1d

    decision = str(snapshot.get("decision", "")).strip().upper()
    if decision != "NO DROP":
        snapshot["explorer_status"] = EXPLORER_STATUS_DEFAULT
        return

    target_z = float(target_position[2]) if len(target_position) >= 3 else 0.0
    context = _make_context(
        mass=payload_config.get("mass", payload_config.get("mass_kg", 1.0)),
        Cd=payload_config.get("cd", payload_config.get("Cd", 0.47)),
        area=payload_config.get("area", payload_config.get("A", 0.01)),
        wind_ref=wind_ref,
        shear=shear,
        target_z=target_z,
        dt=0.01,
    )
    shift_m, miss, feasible = find_release_shift_1d(
        uav_pos,
        uav_vel,
        context,
        target_position,
        target_radius,
    )
    print(f"[EXPLORER] shift={shift_m:.1f} feasible={feasible}")

    if feasible:
        snapshot["suggested_shift_m"] = shift_m
        v_xy = np.asarray(uav_vel, dtype=float)[:2]
        speed = float(np.linalg.norm(v_xy))
        snapshot["suggested_time_s"] = shift_m / speed if speed > 1e-9 else 0.0
        snapshot["explorer_status"] = EXPLORER_STATUS_FEASIBLE
    else:
        snapshot["explorer_status"] = EXPLORER_STATUS_OUT_OF_RANGE


def _resolve_threshold(decision_policy):
    """decision_policy: float (threshold in [0,1]) or str ('Conservative'|'Balanced'|'Aggressive')."""
    if isinstance(decision_policy, (int, float)):
        t = float(decision_policy)
        if not (0 <= t <= 1):
            raise ValueError("probability_threshold must be in [0, 1]")
        return t
    if isinstance(decision_policy, str):
        from configs import mission_configs as cfg
        return cfg.MODE_THRESHOLDS[decision_policy.strip()]
    raise TypeError("decision_policy must be a float (threshold) or a mode name string")


class AdvisoryResult:
    """
    Advisory output for human interpretation. No control actions.
    """

    def __init__(
        self,
        current_feasibility,
        current_P_hit,
        current_cep50_m,
        trend_summary,
        suggested_direction,
        improvement_directions,
        degradation_directions,
    ):
        self.current_feasibility = current_feasibility
        self.current_P_hit = current_P_hit
        self.current_cep50_m = current_cep50_m
        self.trend_summary = trend_summary
        self.suggested_direction = suggested_direction
        self.improvement_directions = tuple(improvement_directions)
        self.degradation_directions = tuple(degradation_directions)


def _p_hit_shifted_target(impact_points, target_pos, target_radius, target_shift_xy):
    """
    Compute P_hit for the same impact points with target shifted by target_shift_xy.
    Vectorized: radial_distances = norm(impact_points - (target + shift), axis=1).
    """
    impact_arr = np.asarray(impact_points, dtype=float)
    if impact_arr.size == 0 or impact_arr.ndim != 2 or impact_arr.shape[1] < 2:
        return 0.0
    target_2d = np.asarray(target_pos, dtype=float).flatten()[:2]
    shifted_target = target_2d + np.asarray(target_shift_xy, dtype=float).reshape(2)
    radial_distances = np.linalg.norm(impact_arr[:, :2] - shifted_target, axis=1)
    hits = int(np.sum(radial_distances <= target_radius))
    return float(hits) / float(impact_arr.shape[0])


def evaluate_advisory(
    snapshot,
    decision_policy,
    position_delta_m=None,
    *,
    trace_mode: str = "advanced",
):
    """
    Evaluate drop feasibility at current UAV state and directional trend using
    base simulation snapshot. No Monte Carlo or propagation; reuses impact_points.

    snapshot: Base simulation result (impact_points, P_hit, cep50, target_position, target_radius).
    decision_policy: float in [0, 1] (threshold) or str ('Conservative'|'Balanced'|'Aggressive').
    position_delta_m: float, position step for directional checks (m). Default ADVISORY_POSITION_DELTA_M.
    trace_mode: unused; kept for API compatibility.

    Returns AdvisoryResult: feasibility at current position, relative trend nearby,
    and suggested direction.
    """
    probability_threshold = _resolve_threshold(decision_policy)
    if position_delta_m is None:
        position_delta_m = ADVISORY_POSITION_DELTA_M
    delta = float(position_delta_m)
    if delta <= 0:
        raise ValueError("position_delta_m must be positive")

    impact_points = snapshot.get("impact_points")
    P_hit_current = float(snapshot.get("P_hit", 0.0) or 0.0)
    cep50_current = float(snapshot.get("cep50", 0.0) or 0.0)
    target_pos = np.asarray(snapshot.get("target_position"), dtype=float).flatten()[:2]
    target_radius = float(snapshot.get("target_radius", 0.0))

    from src import decision_logic
    decision_current = decision_logic.evaluate_drop_decision(
        P_hit_current, probability_threshold
    )

    # UAV +delta X (forward) ~ impact cloud +delta X ~ shift target -delta X
    P_forward = _p_hit_shifted_target(
        impact_points, target_pos, target_radius, (-delta, 0.0)
    )
    P_backward = _p_hit_shifted_target(
        impact_points, target_pos, target_radius, (delta, 0.0)
    )
    P_right = _p_hit_shifted_target(
        impact_points, target_pos, target_radius, (0.0, -delta)
    )
    P_left = _p_hit_shifted_target(
        impact_points, target_pos, target_radius, (0.0, delta)
    )

    best_P = max(P_forward, P_backward, P_right, P_left)
    worst_P = min(P_forward, P_backward, P_right, P_left)

    if best_P > P_hit_current and worst_P < P_hit_current:
        trend_summary = (
            "Feasibility varies with direction. Some nearby positions are "
            "better, some worse."
        )
    elif best_P > P_hit_current:
        trend_summary = (
            "Feasibility improves at least in one direction from the current "
            "position."
        )
    elif worst_P < P_hit_current:
        trend_summary = (
            "Feasibility degrades in all sampled directions from the current "
            "position."
        )
    else:
        trend_summary = (
            "Feasibility is similar in all sampled directions; no strong "
            "trend."
        )

    improvement_directions = []
    degradation_directions = []
    if P_forward > P_hit_current:
        improvement_directions.append("forward (positive X)")
    elif P_forward < P_hit_current:
        degradation_directions.append("forward (positive X)")
    if P_backward > P_hit_current:
        improvement_directions.append("backward (negative X)")
    elif P_backward < P_hit_current:
        degradation_directions.append("backward (negative X)")
    if P_right > P_hit_current:
        improvement_directions.append("right (positive Y)")
    elif P_right < P_hit_current:
        degradation_directions.append("right (positive Y)")
    if P_left > P_hit_current:
        improvement_directions.append("left (negative Y)")
    elif P_left < P_hit_current:
        degradation_directions.append("left (negative Y)")

    if improvement_directions:
        suggested_direction = (
            "Consider moving "
            + " or ".join(improvement_directions)
            + " for higher hit probability."
        )
    else:
        suggested_direction = (
            "No strong directional recommendation from nearby samples; current "
            "position is among the best."
        )

    return AdvisoryResult(
        current_feasibility=decision_current,
        current_P_hit=P_hit_current,
        current_cep50_m=cep50_current,
        trend_summary=trend_summary,
        suggested_direction=suggested_direction,
        improvement_directions=improvement_directions,
        degradation_directions=degradation_directions,
    )
