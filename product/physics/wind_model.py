"""
Wind model with linear shear and vertically correlated stochastic profiles.

Modular interface: future models (log-law, turbulence, gusts) plug in here.
"""

import numpy as np


def wind_linear_shear(
    z: np.ndarray,
    wind_ref: np.ndarray,
    shear: np.ndarray,
) -> np.ndarray:
    """
    Wind vector at altitude z with linear shear.

    w(z) = wind_ref + shear * z[:, None]

    wind_ref is the sampled (stochastic) wind at ground level.
    shear adds a deterministic altitude-dependent component.

    Args:
        z: (N,) altitude array. SI meters.
        wind_ref: (N, 3) reference wind at ground. SI m/s.
        shear: (3,) linear shear coefficient per meter.

    Returns:
        (N, 3) wind vector at each sample's altitude. SI m/s.
    """
    z = np.asarray(z, dtype=float)
    wind_ref = np.asarray(wind_ref, dtype=float)
    shear = np.asarray(shear, dtype=float).reshape(3)
    return wind_ref + shear[None, :] * z[:, None]


def generate_correlated_wind_profile(
    z_levels: np.ndarray,
    wind_ref: np.ndarray,
    wind_std: float,
    correlation_length: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Single vertically correlated wind profile via AR(1).

    W[0] = wind_ref
    W[k] = alpha * W[k-1] + wind_std * sqrt(1 - alpha^2) * eps
    alpha = exp(-dz / correlation_length)

    Noise applied to x, y only; z component decays without noise.

    Args:
        z_levels: (K,) sorted altitude levels. SI meters.
        wind_ref: (3,) base wind vector. SI m/s.
        wind_std: scalar turbulence intensity. SI m/s.
        correlation_length: vertical correlation scale. SI meters. Must be > 0.
        rng: numpy random Generator for reproducibility.

    Returns:
        (K, 3) wind profile at each altitude level.
    """
    z_levels = np.asarray(z_levels, dtype=float)
    wind_ref = np.asarray(wind_ref, dtype=float).reshape(3)
    K = z_levels.shape[0]

    profile = np.zeros((K, 3), dtype=float)
    profile[0] = wind_ref

    for k in range(1, K):
        dz = abs(z_levels[k] - z_levels[k - 1])
        alpha = np.exp(-dz / correlation_length)
        sigma = wind_std * np.sqrt(1.0 - alpha * alpha)
        eps_xy = rng.normal(0.0, 1.0, size=2)
        profile[k, :2] = alpha * profile[k - 1, :2] + sigma * eps_xy
        profile[k, 2] = alpha * profile[k - 1, 2]

    return profile


def generate_correlated_wind_profiles_batch(
    z_levels: np.ndarray,
    wind_ref_batch: np.ndarray,
    wind_std: float,
    correlation_length: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Batch generation of N vertically correlated wind profiles.

    Vectorized over N samples; sequential over K altitude levels (AR(1) dependency).
    Loop count is K (typically 10-40), not N.

    Args:
        z_levels: (K,) sorted altitude levels. SI meters.
        wind_ref_batch: (N, 3) per-sample base wind at ground. SI m/s.
        wind_std: scalar turbulence intensity. SI m/s.
        correlation_length: vertical correlation scale. SI meters. Must be > 0.
        rng: numpy random Generator for reproducibility.

    Returns:
        (N, K, 3) wind profiles. profiles[n, k, :] = wind for sample n at level k.
    """
    z_levels = np.asarray(z_levels, dtype=float)
    wind_ref_batch = np.asarray(wind_ref_batch, dtype=float)
    K = z_levels.shape[0]
    N = wind_ref_batch.shape[0]

    noise = rng.normal(0.0, 1.0, size=(K - 1, N, 2))

    profiles = np.zeros((N, K, 3), dtype=float)
    profiles[:, 0, :] = wind_ref_batch

    for k in range(1, K):
        dz = abs(z_levels[k] - z_levels[k - 1])
        alpha = np.exp(-dz / correlation_length)
        sigma = wind_std * np.sqrt(1.0 - alpha * alpha)
        profiles[:, k, :2] = alpha * profiles[:, k - 1, :2] + sigma * noise[k - 1]
        profiles[:, k, 2] = alpha * profiles[:, k - 1, 2]

    return profiles


def generate_wind_drift_batch(
    rng: np.random.Generator,
    n_samples: int,
    drift_amplitude: float = 0.5,
    drift_period: float = 10.0,
) -> tuple[np.ndarray, float, np.ndarray]:
    """
    Generate per-sample temporal wind drift parameters for sinusoidal model.

    delta_W_n(t) = amp_n * sin(omega * t + phase_n)

    Amplitude is drawn per-sample per-axis from N(0, drift_amplitude).
    Phase is uniform in [0, 2*pi) per-sample per-axis.
    Omega (angular frequency) is shared: 2*pi / drift_period.

    Args:
        rng: seeded Generator.
        n_samples: number of MC samples.
        drift_amplitude: 1-sigma of amplitude Gaussian (m/s).
        drift_period: period of sinusoidal drift (s).

    Returns:
        (amp, omega, phase) where amp: (N,3), omega: float, phase: (N,3).
    """
    amp = rng.normal(0.0, drift_amplitude, size=(n_samples, 3))
    omega = 2.0 * np.pi / max(drift_period, 1e-6)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=(n_samples, 3))
    return amp, omega, phase


def interpolate_wind_profiles(
    z: np.ndarray,
    z_levels: np.ndarray,
    profiles: np.ndarray,
) -> np.ndarray:
    """
    Vectorized linear interpolation of wind from precomputed profiles.

    Args:
        z: (M,) altitudes of active samples.
        z_levels: (K,) sorted altitude levels.
        profiles: (M, K, 3) wind profiles for the M samples.

    Returns:
        (M, 3) interpolated wind at each sample's altitude.
    """
    M = z.shape[0]
    z_clamped = np.clip(z, z_levels[0], z_levels[-1])
    idx = np.searchsorted(z_levels, z_clamped, side="right") - 1
    idx = np.clip(idx, 0, len(z_levels) - 2)
    dz = z_levels[idx + 1] - z_levels[idx]
    t = np.where(dz > 0, (z_clamped - z_levels[idx]) / dz, 0.0)
    arange_m = np.arange(M)
    w0 = profiles[arange_m, idx, :]
    w1 = profiles[arange_m, idx + 1, :]
    return w0 + t[:, None] * (w1 - w0)
