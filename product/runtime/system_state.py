"""
Runtime system state for SCYTHE.

Holds shared state between telemetry, planner, guidance, and UI loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional

import numpy as np

from product.aircraft import VehicleState
from product.explorer import ReleaseEnvelopeResult
from product.guidance.corridor_guidance import GuidanceResult


@dataclass
class SystemState:
    """
    Shared runtime state container.

    This object is intended to be shared between multiple loops running in
    different threads. Use lock when reading or writing vehicle_state,
    target_position, guidance_result, or envelope_result.
    """

    vehicle_state: Optional[VehicleState] = None
    target_position: Optional[np.ndarray] = None
    guidance_result: Optional[GuidanceResult] = None
    envelope_result: Optional[ReleaseEnvelopeResult] = None
    envelope_dirty: bool = True
    running: bool = True
    monte_carlo_running: bool = False
    mission_committed: bool = False
    impact_data_version: int = 0
    settings: Dict[str, Any] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

