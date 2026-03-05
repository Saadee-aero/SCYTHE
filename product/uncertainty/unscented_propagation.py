"""
Unscented Transform-based deterministic payload propagation.

This module constructs an uncertainty state over key environment and
release parameters, generates sigma points, and propagates each sigma
state through the existing RK2 payload integrator. The result is a
Gaussian approximation to the impact-point distribution.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from product.uncertainty import build_uncertainty_model, generate_sigma_points
from src.monte_carlo import _propagate_payload_batch


def propagate_unscented(
    context,
    config,
    pos0,
    vel0,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Propagate Unscented Transform sigma points through RK2 payload dynamics.

    Parameters
    ----------
    context :
        PropagationContext instance (immutable) defining physics and wind model.
    config :
        Configuration object providing UT uncertainty parameters.
    pos0 : array_like, shape (3,)
        Nominal initial position.
    vel0 : array_like, shape (3,)
        Nominal initial velocity.

    Returns
    -------
    impact_mean : (2,)
        Mean impact point (x, y) across all sigma trajectories.
    impact_cov : (2, 2)
        Impact-point covariance matrix.
    """
    # 1) Build Gaussian uncertainty model for UT state.
    mu, Sigma = build_uncertainty_model(context, config)

    # 2) Generate sigma points and weights.
    sigma_points, Wm, Wc = generate_sigma_points(mu, Sigma)
    # Ensure mean weights are numerically normalized (defensive).
    Wm = Wm / np.sum(Wm)

    pos0 = np.asarray(pos0, dtype=float).reshape(3)
    vel0 = np.asarray(vel0, dtype=float).reshape(3)

    # Base wind reference from context; ensure 3-vector.
    base_wind = np.asarray(context.wind_ref, dtype=float).reshape(-1)[:3]

    num_sigma = sigma_points.shape[0]
    impacts = np.zeros((num_sigma, 2), dtype=float)

    for i in range(num_sigma):
        u = sigma_points[i]
        wind_bias_x = float(u[0])
        wind_bias_y = float(u[1])
        release_x_err = float(u[2])
        release_y_err = float(u[3])
        velocity_bias = float(u[4])

        # Smooth saturation of wind biases to preserve sigma-point structure.
        max_wind_bias = 6.0  # m/s
        wind_bias_x = float(max_wind_bias * np.tanh(wind_bias_x / max_wind_bias))
        wind_bias_y = float(max_wind_bias * np.tanh(wind_bias_y / max_wind_bias))

        # Wind bias: adjust x/y components of wind_ref.
        wind_vec = base_wind.copy()
        wind_vec[0] += wind_bias_x
        wind_vec[1] += wind_bias_y
        # Context expects per-sample wind_ref; use N=1.
        wind_ref_ut = wind_vec.reshape(1, 3)
        ctx_ut = context.with_wind(wind_ref_ut)

        # Clamp release errors to a reasonable physical envelope.
        max_release_error = 50.0  # meters
        release_x_err = float(np.clip(release_x_err, -max_release_error, max_release_error))
        release_y_err = float(np.clip(release_y_err, -max_release_error, max_release_error))

        # Release position errors in world frame (x, y).
        pos_ut = pos0.copy()
        pos_ut[0] += release_x_err
        pos_ut[1] += release_y_err

        # Velocity magnitude bias along current direction, with floor to
        # prevent unrealistic inversion or near-zero speeds.
        scale = 1.0 + velocity_bias
        scale = max(scale, 0.1)
        vel_ut = vel0.copy() * scale

        # 3) Propagate payload trajectory with existing RK2 integrator.
        impact_xy, _ = _propagate_payload_batch(
            ctx_ut,
            pos_ut,
            vel_ut,
            return_trajectories=False,
            return_impact_speeds=False,
        )

        # _propagate_payload_batch returns impact_xy of shape (N, 2) with N=1.
        impacts[i] = np.asarray(impact_xy, dtype=float).reshape(1, 2)[0]

    # 4) Combine results into mean and covariance.
    # Mean impact point.
    impact_mean = np.sum(Wm[:, None] * impacts, axis=0)

    # Impact covariance: sum over weighted outer products.
    diff = impacts - impact_mean[None, :]
    impact_cov = np.zeros((2, 2), dtype=float)
    for i in range(num_sigma):
        v = diff[i]
        impact_cov += Wc[i] * np.outer(v, v)

    # Small diagonal stabilization to improve numerical robustness in
    # downstream eigen/square-root computations.
    impact_cov += 1e-12 * np.eye(2, dtype=float)

    return impact_mean, impact_cov

