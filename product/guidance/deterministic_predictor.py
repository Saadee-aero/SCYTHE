"""
Deterministic impact predictor for fast feasibility screening.

Single-trajectory propagation with zero stochastic wind.
Uses same physics and RK2 integrator as Monte Carlo.
No sampling, no RNG, minimal runtime.
"""
from __future__ import annotations

import os
import time

import numpy as np

from src.monte_carlo import _compute_acceleration

# Gravity vector (SI) — matches monte_carlo
_GRAVITY = np.array([0.0, 0.0, -9.81], dtype=float)

_DEFAULT_DT = 0.01
_MAX_STEPS = 25000


def predict_mean_impact(
    uav_pos,
    uav_vel,
    context,
) -> tuple[np.ndarray, float]:
    """
    Propagate single trajectory (no stochastic wind).
    Use RK2 integrator identical to Monte Carlo.

    Args:
        uav_pos: (3,) release position. SI meters.
        uav_vel: (3,) release velocity. SI m/s.
        context: PropagationContext with mass, Cd, area, wind, target_z, dt.

    Returns:
        impact_xy: (2,) numpy array [x, y] at impact.
        flight_time: float seconds.
    """
    pos = np.asarray(uav_pos, dtype=float).reshape(3).copy()
    vel = np.asarray(uav_vel, dtype=float).reshape(3).copy()
    ground_z = float(context.target_z)

    if pos[2] <= ground_z:
        return np.array([pos[0], pos[1]], dtype=float), 0.0

    dt = float(context.dt)

    t_sim = 0.0
    step = 0
    _debug = bool(os.environ.get("AIRDROP_DEBUG"))
    if _debug:
        t0 = time.perf_counter()

    while pos[2] > ground_z and step < _MAX_STEPS:
        p_a = pos.reshape(1, 3)
        v_a = vel.reshape(1, 3)
        a1 = _compute_acceleration(p_a, v_a, context)
        a1 = a1[0]

        v_temp = vel + a1 * dt
        p_temp = pos + vel * dt

        p_temp_a = p_temp.reshape(1, 3)
        v_temp_a = v_temp.reshape(1, 3)
        a2 = _compute_acceleration(p_temp_a, v_temp_a, context)
        a2 = a2[0]

        # RK2 (Heun): corrector step
        pos = pos + 0.5 * (vel + v_temp) * dt
        vel = vel + 0.5 * (a1 + a2) * dt

        t_sim += dt
        step += 1

    if _debug:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"[DETERMINISTIC] steps={step} time_ms={elapsed_ms:.2f}")

    impact_xy = np.array([pos[0], pos[1]], dtype=float)
    return impact_xy, float(t_sim)
