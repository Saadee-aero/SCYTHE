"""
Uncertainty state model for Unscented Transform-based inference.

Defines a 5-dimensional Gaussian state:

    u = [
        wind_bias_x,
        wind_bias_y,
        release_x_error,
        release_y_error,
        velocity_bias,
    ]

and constructs its mean vector µ and covariance matrix Σ based on
propagation context and configuration.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np


WIND_CORRELATION_RHO = 0.3
# Correlation between wind_x and wind_y bias errors.
# Justified by heading estimation uncertainty (sigma_theta ~ 17 deg).
# ρ=0.3 is conservative. Range [0, 1). Must satisfy rho < 1.
# Off-diagonal term: Sigma[0,1] = rho * var_wind.


def build_uncertainty_model(context, config) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build Gaussian uncertainty model (µ, Σ) for the Unscented Transform state.

    State components:
        0: wind_bias_x
        1: wind_bias_y
        2: release_x_error
        3: release_y_error
        4: velocity_bias

    Conventions:
        - velocity_bias represents a fractional magnitude bias applied along
          the current velocity direction, e.g. during propagation:
              v_effective = v_nominal * (1 + velocity_bias)
        - release_x_error and release_y_error are horizontal position errors
          in the world frame. When sigma points are injected into the
          propagation, these must be interpreted relative to the UAV heading
          to produce forward and cross-track release errors.

    The wind bias variance is altitude-dependent with saturation:
        σ_wind(z) = clamp(σ0 + k * z, 0, σ_max)

    where z is the release altitude, taken from the propagation context
    when available.

    Diagonal covariance entries:
        Var(wind_bias_x)      = σ_wind²
        Var(wind_bias_y)      = σ_wind²
        Var(release_x_error)  = release_pos_sigma²
        Var(release_y_error)  = release_pos_sigma²
        Var(velocity_bias)    = velocity_sigma²

    Off-diagonal terms are currently zero.
    """
    # Release altitude: prefer explicit attribute if present; otherwise fall
    # back to target_z (ground elevation) or 0.0. This keeps the function
    # usable even before the context carries a dedicated release altitude.
    if hasattr(context, "release_altitude"):
        z_release = float(getattr(context, "release_altitude"))
    else:
        z_release = float(getattr(context, "target_z", 0.0))
    # Use absolute altitude above ground for robustness.
    z_release = max(0.0, z_release)

    # Wind uncertainty profile σ_wind(z) = clamp(σ0 + k * z, 0, σ_max)
    sigma0 = float(getattr(config, "wind_sigma0", 0.0))
    k_alt = float(getattr(config, "wind_sigma_altitude_coeff", 0.0))
    sigma_max = float(getattr(config, "wind_sigma_max", 4.0))
    sigma_wind = sigma0 + k_alt * z_release
    sigma_wind = max(0.0, sigma_wind)
    sigma_wind = min(sigma_wind, sigma_max)

    # Release position and velocity uncertainty scales
    release_pos_sigma = float(getattr(config, "release_pos_sigma", 0.0))
    velocity_sigma = float(getattr(config, "velocity_sigma", 0.0))

    # Mean vector (all zero biases by default).
    mu = np.zeros(5, dtype=float)

    # Covariance matrix Σ (5x5) with diagonal entries defined above.
    # Apply a small variance floor for numerical stability (e.g. Cholesky).
    variance_floor = 1e-8
    var_wind = max(sigma_wind ** 2, variance_floor)
    var_release = max(release_pos_sigma ** 2, variance_floor)
    var_velocity = max(velocity_sigma ** 2, variance_floor)

    Sigma = np.zeros((5, 5), dtype=float)
    Sigma[0, 0] = var_wind       # wind_bias_x
    Sigma[1, 1] = var_wind       # wind_bias_y
    Sigma[2, 2] = var_release    # release_x_error
    Sigma[3, 3] = var_release    # release_y_error
    Sigma[4, 4] = var_velocity   # velocity_bias

    # Wind x/y correlation from heading uncertainty (CLAUDE.md §7.9 note)
    # Sigma[0,1] = Sigma[1,0] = WIND_CORRELATION_RHO * var_wind
    rho_wind = WIND_CORRELATION_RHO * float(var_wind)
    Sigma[0, 1] = rho_wind
    Sigma[1, 0] = rho_wind

    # PD check: for 2x2 wind block, det = var_wind^2 * (1 - rho^2)
    # At rho=0.3: det = var_wind^2 * 0.91 > 0 — confirmed PD

    # Enforce exact symmetry for numerical robustness (e.g. Cholesky).
    Sigma = 0.5 * (Sigma + Sigma.T)

    # Guard: catch any future rho >= 1 misconfiguration
    assert WIND_CORRELATION_RHO < 1.0, \
        f"WIND_CORRELATION_RHO must be < 1 for PD matrix, got {WIND_CORRELATION_RHO}"

    return mu, Sigma

