"""
Unscented Transform sigma point generator for Gaussian state distributions.

Given mean µ and covariance Σ, generates 2n + 1 sigma points and their
associated mean and covariance weights using standard UT parameters.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np


def generate_sigma_points(
    mu: np.ndarray,
    Sigma: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Generate Unscented Transform sigma points and weights.

    Parameters
    ----------
    mu : (n,)
        Mean state vector.
    Sigma : (n, n)
        State covariance matrix (symmetric, positive definite).

    Returns
    -------
    sigma_points : (2n + 1, n)
        Matrix of sigma points.
    weights_mean : (2n + 1,)
        Weights for recovering the mean.
    weights_cov : (2n + 1,)
        Weights for recovering the covariance.
    """
    mu = np.asarray(mu, dtype=float).reshape(-1)
    Sigma = np.asarray(Sigma, dtype=float)

    n = mu.shape[0]
    if Sigma.shape != (n, n):
        raise ValueError(f"Sigma must have shape ({n}, {n}); got {Sigma.shape}")

    # Unscented Transform parameters tuned for nonlinear ballistic propagation.
    alpha = 0.1
    beta = 2.0
    kappa = 0.0

    lam = alpha ** 2 * (n + kappa) - n
    scale = n + lam
    if scale <= 0.0:
        raise ValueError("Unscented scaling factor (n + lambda) must be positive")
    gamma = float(np.sqrt(scale))

    # Matrix square root via Cholesky decomposition with tiny jitter
    # to improve robustness when Sigma is nearly singular.
    epsilon = 1e-12
    Sigma_jittered = Sigma + epsilon * np.eye(n, dtype=float)
    try:
        S = np.linalg.cholesky(Sigma_jittered)
    except np.linalg.LinAlgError as e:
        raise ValueError("Covariance matrix Sigma must be symmetric positive definite") from e

    # Allocate sigma points
    num_sigma = 2 * n + 1
    sigma_points = np.zeros((num_sigma, n), dtype=float)
    sigma_points[0] = mu

    for i in range(n):
        col = gamma * S[:, i]
        sigma_points[i + 1] = mu + col
        sigma_points[i + 1 + n] = mu - col

    # Weights for mean and covariance
    weights_mean = np.full(num_sigma, 1.0 / (2.0 * scale), dtype=float)
    weights_cov = np.full(num_sigma, 1.0 / (2.0 * scale), dtype=float)

    weights_mean[0] = lam / scale
    weights_cov[0] = weights_mean[0] + (1.0 - alpha ** 2 + beta)

    # Enforce numerical normalization of mean weights.
    weights_mean /= np.sum(weights_mean)

    return sigma_points, weights_mean, weights_cov

