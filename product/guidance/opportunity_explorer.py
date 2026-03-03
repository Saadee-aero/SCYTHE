"""
1D along-track opportunity explorer.

Suggests forward/backward shift along UAV velocity when drop not feasible.
Deterministic only; uses mean-physics predictor.
Search window derived from mission physics.
"""
from __future__ import annotations

import numpy as np

from product.guidance.deterministic_predictor import predict_mean_impact


def _miss_distance(impact_xy: np.ndarray, target_2d: np.ndarray, target_radius: float) -> float:
    """Signed miss: positive = outside target, negative = inside."""
    d = float(np.linalg.norm(impact_xy - target_2d))
    return d - target_radius


_DEFAULT_SMALL_SEARCH_M = 20.0
_HORIZONTAL_SPEED_THRESHOLD = 0.1  # m/s
_REACH_SAFETY_FACTOR = 1.3


def find_release_shift_1d(
    uav_pos,
    uav_vel,
    context,
    target_position,
    target_radius: float,
    max_iterations: int = 8,
    tolerance_m: float = 0.5,
) -> tuple[float, float, bool]:
    """
    Find suggested release shift along UAV horizontal velocity for feasibility.
    Search window derived from mission physics. Context supplied by advisory layer.

    Args:
        uav_pos: (3,) release position. SI meters.
        uav_vel: (3,) release velocity. SI m/s.
        context: propagation context (built by advisory layer).
        target_position: (3,) target center. target_position[2] = ground level.
        target_radius: target radius. SI meters.
        max_iterations: bisection iterations.
        tolerance_m: root-finding tolerance in meters. Stop when interval width < tolerance_m.

    Returns:
        suggested_shift_m: meters along velocity (positive = forward).
        estimated_miss_distance: signed miss at suggested shift.
        feasible: True if a shift exists where miss <= 0.
    """
    pos = np.asarray(uav_pos, dtype=float).reshape(3)
    vel = np.asarray(uav_vel, dtype=float).reshape(3)
    target_2d = np.asarray(target_position, dtype=float).flatten()[:2]
    target_radius = float(target_radius)

    v_xy = vel[:2]
    v_norm = float(np.linalg.norm(v_xy))
    if v_norm < 1e-9:
        impact, _ = predict_mean_impact(pos, vel, context)
        miss = _miss_distance(impact, target_2d, target_radius)
        return 0.0, miss, miss <= 0
    unit = v_xy / v_norm

    # One predictor call for reach estimation; search window from mission physics
    impact_center, flight_time = predict_mean_impact(pos, vel, context)
    miss_center = _miss_distance(impact_center, target_2d, target_radius)
    horizontal_speed = v_norm
    if horizontal_speed < _HORIZONTAL_SPEED_THRESHOLD:
        max_search_distance = _DEFAULT_SMALL_SEARCH_M
    else:
        reach = horizontal_speed * flight_time
        max_search_distance = reach * _REACH_SAFETY_FACTOR

    if max_search_distance < 150:
        n_points = 7
    elif max_search_distance < 400:
        n_points = 9
    else:
        n_points = 11

    def eval_miss(shift_m: float) -> float:
        if abs(shift_m) < 1e-12:
            return miss_center
        pos_shifted = pos.copy()
        pos_shifted[0] += shift_m * unit[0]
        pos_shifted[1] += shift_m * unit[1]
        impact_xy, _ = predict_mean_impact(pos_shifted, vel, context)
        return _miss_distance(impact_xy, target_2d, target_radius)

    grid_points = np.linspace(
        -max_search_distance,
        max_search_distance,
        n_points,
    )
    shifts = grid_points
    misses = [eval_miss(s) for s in shifts]
    center_idx = len(shifts) // 2  # index of 0
    if misses[center_idx] <= 0:
        return 0.0, misses[center_idx], True
    for i, m in enumerate(misses):
        if m <= 0:
            best = shifts[i]
            return best, m, True
    for i in range(len(shifts) - 1):
        if np.sign(misses[i]) != np.sign(misses[i + 1]):
            lo, hi = shifts[i], shifts[i + 1]
            miss_lo = misses[i]
            break
    else:
        return 0.0, misses[center_idx], False

    for _ in range(max_iterations):
        if abs(hi - lo) < tolerance_m:
            break
        mid = 0.5 * (lo + hi)
        miss_mid = eval_miss(mid)
        if abs(miss_mid) < tolerance_m:
            break
        # Narrow interval; avoid floating-point sign ambiguity via np.sign
        if np.sign(miss_mid) == np.sign(miss_lo):
            lo, miss_lo = mid, miss_mid
        else:
            hi = mid

    final_shift = 0.5 * (lo + hi)
    final_miss = eval_miss(final_shift)
    return final_shift, final_miss, final_miss <= 0
