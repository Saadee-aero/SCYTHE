"""
Vehicle state representation for aircraft/UAV kinematics.

Encapsulates position, velocity, acceleration, and timestamp with validation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VehicleState:
    """Kinematic state of a vehicle (position, velocity, acceleration) at a timestamp."""

    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    timestamp: float

    def __post_init__(self) -> None:
        self.position = np.asarray(self.position, dtype=float).reshape(3)
        self.velocity = np.asarray(self.velocity, dtype=float).reshape(3)
        self.acceleration = np.asarray(self.acceleration, dtype=float).reshape(3)
        self.timestamp = float(self.timestamp)

        for name, arr in [
            ("position", self.position),
            ("velocity", self.velocity),
            ("acceleration", self.acceleration),
        ]:
            if arr.shape != (3,):
                raise ValueError(
                    "%s must have shape (3,), got %s" % (name, arr.shape)
                )
