"""
Sensor noise injection layer (Phase 1B).

Separates environment truth from measured state by injecting Gaussian
measurement uncertainty into initial conditions before Monte Carlo
propagation.  The propagation physics are NOT modified; noise is applied
once per sample to the initial pos/vel vectors.

Usage:
    model = SensorModel(wind_sigma=0.5, velocity_sigma=0.3,
                        altitude_sigma=2.0, release_sigma=0.002)
    pos_batch, vel_batch, dt_offsets = model.perturb_batch(
        pos0, vel0, n_samples, rng)
"""

from __future__ import annotations

import numpy as np


class SensorModel:
    """Gaussian sensor-noise model for measurement uncertainty injection.

    Each sigma is a 1-sigma standard deviation applied independently per axis
    unless noted otherwise.

    Parameters
    ----------
    wind_sigma : float
        1-sigma wind measurement noise (m/s), applied per-axis to ground-level
        wind samples.  Handled externally by the caller (wind samples are drawn
        separately in run_monte_carlo).  Stored here for API completeness but
        NOT consumed by ``perturb_batch``.
    velocity_sigma : float
        1-sigma UAV velocity measurement noise (m/s), applied per-axis.
    altitude_sigma : float
        1-sigma UAV altitude (z) measurement noise (m).
    release_sigma : float
        1-sigma release-timing jitter (s).  Converted to a position offset via
        ``vel * dt_jitter`` inside ``perturb_batch``.
    """

    def __init__(
        self,
        wind_sigma: float = 0.0,
        velocity_sigma: float = 0.0,
        altitude_sigma: float = 0.0,
        release_sigma: float = 0.0,
    ) -> None:
        self.wind_sigma = float(wind_sigma)
        self.velocity_sigma = float(velocity_sigma)
        self.altitude_sigma = float(altitude_sigma)
        self.release_sigma = float(release_sigma)

    @property
    def is_zero(self) -> bool:
        """True when all sigmas are zero (no-op fast path)."""
        return (
            self.wind_sigma == 0.0
            and self.velocity_sigma == 0.0
            and self.altitude_sigma == 0.0
            and self.release_sigma == 0.0
        )

    def perturb_batch(
        self,
        pos0: np.ndarray,
        vel0: np.ndarray,
        n_samples: int,
        rng: np.random.Generator,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate per-sample perturbed initial conditions.

        Parameters
        ----------
        pos0 : (3,) truth position.
        vel0 : (3,) truth velocity.
        n_samples : number of MC samples.
        rng : seeded Generator (preserves determinism).

        Returns
        -------
        pos_batch : (N, 3) perturbed positions.
        vel_batch : (N, 3) perturbed velocities.
        """
        pos0 = np.asarray(pos0, dtype=float).reshape(3)
        vel0 = np.asarray(vel0, dtype=float).reshape(3)
        N = int(n_samples)

        if self.is_zero:
            return (
                np.broadcast_to(pos0, (N, 3)).copy(),
                np.broadcast_to(vel0, (N, 3)).copy(),
            )

        pos_batch = np.broadcast_to(pos0, (N, 3)).copy()
        vel_batch = np.broadcast_to(vel0, (N, 3)).copy()

        # Altitude noise (z only)
        if self.altitude_sigma > 0:
            pos_batch[:, 2] += rng.normal(0.0, self.altitude_sigma, size=N)

        # Velocity noise (all axes)
        if self.velocity_sigma > 0:
            vel_batch += rng.normal(0.0, self.velocity_sigma, size=(N, 3))

        # Release-timing jitter -> position offset along velocity vector
        if self.release_sigma > 0:
            dt_jitter = rng.normal(0.0, self.release_sigma, size=(N, 1))
            pos_batch += vel_batch * dt_jitter

        return pos_batch, vel_batch

    def sample_release_delay(self, rng: np.random.Generator) -> float:
        """Sample a single scalar release-timing delay (s)."""
        if self.release_sigma <= 0:
            return 0.0
        return float(rng.normal(0.0, self.release_sigma))

    def sample_measured_state(
        self,
        truth_state: dict,
        rng: np.random.Generator,
    ) -> dict:
        """Return a single perturbed measurement of the truth state.

        Parameters
        ----------
        truth_state : dict with keys ``uav_pos`` (3,), ``uav_vel`` (3,).
        rng : seeded Generator.

        Returns
        -------
        dict with same keys, values perturbed by sensor noise.
        """
        pos = np.asarray(truth_state["uav_pos"], dtype=float).copy()
        vel = np.asarray(truth_state["uav_vel"], dtype=float).copy()

        if self.altitude_sigma > 0:
            pos[2] += rng.normal(0.0, self.altitude_sigma)
        if self.velocity_sigma > 0:
            vel += rng.normal(0.0, self.velocity_sigma, size=3)
        if self.release_sigma > 0:
            dt_j = rng.normal(0.0, self.release_sigma)
            pos += vel * dt_j

        return {"uav_pos": tuple(pos), "uav_vel": tuple(vel)}
