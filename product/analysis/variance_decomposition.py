"""
Phase 2 — Conditional Monte Carlo Variance Decomposition.

Computes the true uncertainty contribution of each noise source (wind,
release timing, UAV velocity) by running conditional MC experiments with
exactly one noise channel active at a time.

Each case runs a reduced-N MC (default 500) and measures the variance of
radial miss distance.  Contributions are then normalised to sum to 1.

This module calls ``run_monte_carlo`` directly — no nested propagation,
no extra integrator calls beyond the three conditional runs.
"""

from __future__ import annotations

from typing import Any

import numpy as np


# Default per-source sigmas used when the snapshot/overrides don't specify them.
_DEFAULT_RELEASE_SIGMA = 0.01   # 10 ms
_DEFAULT_VELOCITY_SIGMA = 0.3   # m/s


def compute_uncertainty_contributions(
    snapshot: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    N: int = 500,
    *,
    mc_call_counter: list | None = None,
) -> dict[str, float] | None:
    """Run three conditional MC experiments and return normalised variance weights.

    Parameters
    ----------
    snapshot : dict
        Latest simulation snapshot (must contain at least ``target_position``
        and the physical parameters used for the baseline run).
    overrides : dict, optional
        UI config overrides (same dict passed to ``run_simulation_snapshot``).
    N : int
        Sample count for each conditional run (default 500).
    mc_call_counter : list, optional
        Shared MC call counter (incremented per call).

    Returns
    -------
    dict with keys ``"wind"``, ``"release"``, ``"velocity"`` (fractions summing
    to ~1.0), or ``None`` if computation could not proceed.
    """
    from configs import mission_configs as cfg
    from src.monte_carlo import run_monte_carlo
    from product.guidance.advisory_layer import build_propagation_context
    from product.uncertainty.sensor_model import SensorModel

    overrides = overrides or {}
    if mc_call_counter is None:
        mc_call_counter = [0]

    target_pos = snapshot.get("target_position")
    if target_pos is None:
        return None
    target_2d = np.asarray(target_pos, dtype=float).flatten()[:2]

    # --- Extract physical state from snapshot / overrides / config ---
    pos0 = (
        float(overrides.get("uav_x", cfg.uav_pos[0])),
        float(overrides.get("uav_y", cfg.uav_pos[1])),
        float(overrides.get("uav_altitude", cfg.uav_pos[2])),
    )
    vel0 = (
        float(overrides.get("uav_vx", cfg.uav_vel[0])),
        float(overrides.get("uav_vy", cfg.uav_vel[1])),
        float(overrides.get("uav_vz", cfg.uav_vel[2])),
    )
    mass = float(overrides.get("mass", cfg.mass))
    Cd = float(overrides.get("cd", cfg.Cd))
    A = float(overrides.get("area", cfg.A))
    rho = cfg.rho
    wind_mean = (
        float(overrides.get("wind_x", overrides.get("wind_mean_x", cfg.wind_mean[0]))),
        float(overrides.get("wind_y", overrides.get("wind_mean_y", cfg.wind_mean[1]))),
        float(cfg.wind_mean[2] if len(cfg.wind_mean) > 2 else 0.0),
    )
    wind_std = float(overrides.get("wind_std", cfg.wind_std))
    dt = cfg.dt
    _raw_seed = overrides.get("random_seed", cfg.RANDOM_SEED)
    seed = 42 if _raw_seed is None else int(_raw_seed)
    target_z_val = float(target_pos[2]) if len(target_pos) >= 3 else 0.0

    release_sigma = float(overrides.get("release_sigma", _DEFAULT_RELEASE_SIGMA))
    velocity_sigma = float(overrides.get("velocity_sigma", _DEFAULT_VELOCITY_SIGMA))

    def _radial_variance(impacts: np.ndarray) -> float:
        dists = np.linalg.norm(impacts[:, :2] - target_2d, axis=1)
        return float(np.var(dists))

    def _run(ws: float, rs: float | None, sm: SensorModel | None) -> float:
        mc_call_counter[0] += 1
        cfg_dict = {"n_samples": N}
        ctx = build_propagation_context(mass, Cd, A, wind_mean, None, target_z_val, dt)
        imp = run_monte_carlo(
            ctx, pos0, vel0, ws, cfg_dict, seed,
            sensor_model=sm,
            release_sigma=rs,
            caller="BASE",
            mode="advanced",
        )
        return _radial_variance(imp)

    # Case A — wind only
    var_wind = _run(ws=wind_std, rs=None, sm=None)

    # Case B — release jitter only
    var_release = _run(ws=0.0, rs=release_sigma, sm=None)

    # Case C — velocity noise only
    vel_model = SensorModel(velocity_sigma=velocity_sigma)
    var_velocity = _run(ws=0.0, rs=None, sm=vel_model)

    total = var_wind + var_release + var_velocity
    if total < 1e-12:
        return {"wind": 1.0 / 3.0, "release": 1.0 / 3.0, "velocity": 1.0 / 3.0}

    return {
        "wind": var_wind / total,
        "release": var_release / total,
        "velocity": var_velocity / total,
    }
