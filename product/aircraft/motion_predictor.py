"""
Constant-acceleration motion prediction for vehicle kinematics.

Extrapolates position and velocity from a VehicleState to a future timestamp.
"""

from __future__ import annotations

import numpy as np

from product.aircraft.vehicle_state import VehicleState


class MotionPredictor:
    """Predicts future vehicle state using constant-acceleration kinematics."""

    def __init__(self, vehicle_state: VehicleState) -> None:
        self._state = vehicle_state

    @property
    def vehicle_state(self) -> VehicleState:
        """Current vehicle state used for prediction."""
        return self._state

    def predict_state(self, t_future: float) -> tuple[np.ndarray, np.ndarray]:
        """
        Compute predicted position and velocity at absolute time t_future.

        Uses constant-acceleration kinematics:
            position = pos + vel*dt + 0.5*acc*dt**2
            velocity = vel + acc*dt

        Parameters
        ----------
        t_future : float
            Absolute future time.

        Returns
        -------
        position : np.ndarray, shape (3,)
        velocity : np.ndarray, shape (3,)
        """
        dt = float(t_future) - self._state.timestamp
        dt = max(0.0, dt)
        pos = self._state.position
        vel = self._state.velocity
        acc = self._state.acceleration

        position = pos + vel * dt + 0.5 * acc * (dt ** 2)
        velocity = vel + acc * dt

        return (
            np.asarray(position, dtype=float).reshape(3),
            np.asarray(velocity, dtype=float).reshape(3),
        )
