"""
Numerical diagnostics utilities for AIRDROP-X.
These checks are advisory and do not modify engine logic.
"""

from __future__ import annotations

from typing import Dict, Any


def quick_stability_check(
    random_seed: int,
    dt: float,
    samples: int = 5,
) -> Dict[str, Any]:
    """
    Compare CEP50 at dt and dt/2 over a small sample set.
    PASS when relative error < 2%, else CAUTION.
    """
    from configs import mission_configs as cfg
    from product.guidance.advisory_layer import build_propagation_context
    from src import monte_carlo, metrics

    n = int(samples)
    dt_base = float(dt)
    dt_half = dt_base / 2.0 if dt_base > 0 else dt_base

    target_z = float(cfg.target_pos[2]) if len(cfg.target_pos) >= 3 else 0.0
    cfg_dict = {"n_samples": n}

    ctx_dt = build_propagation_context(cfg.mass, cfg.Cd, cfg.A, cfg.wind_mean, None, target_z, dt_base)
    ctx_half = build_propagation_context(cfg.mass, cfg.Cd, cfg.A, cfg.wind_mean, None, target_z, dt_half)
    impact_dt = monte_carlo.run_monte_carlo(ctx_dt, cfg.uav_pos, cfg.uav_vel, cfg.wind_std, cfg_dict, random_seed)
    impact_half = monte_carlo.run_monte_carlo(ctx_half, cfg.uav_pos, cfg.uav_vel, cfg.wind_std, cfg_dict, random_seed)

    cep_dt = metrics.compute_cep50(impact_dt, cfg.target_pos)
    cep_half = metrics.compute_cep50(impact_half, cfg.target_pos)
    denom = max(abs(cep_half), 1e-9)
    rel_err = abs(cep_dt - cep_half) / denom
    status = "PASS" if rel_err < 0.02 else "CAUTION"

    return {
        "integration_method": "Explicit Euler",
        "dt": dt_base,
        "samples": n,
        "relative_error": float(rel_err),
        "status": status,
    }

