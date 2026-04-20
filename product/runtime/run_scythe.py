"""
Startup script for SCYTHE runtime loops.

Wires together:
  - TelemetryLoop (50 Hz)
  - GuidanceLoop (12 Hz)
  - UIRenderLoop (30 Hz)
  - BackgroundPlannerLoop (1 Hz)

and runs until KeyboardInterrupt.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root is on the module path (run_scythe lives in product/runtime/).
_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import numpy as np

from product.runtime.runtime_loops import (
    BackgroundPlannerLoop,
    GuidanceLoop,
    TelemetryLoop,
    UIRenderLoop,
)
from product.runtime.system_state import SystemState
from product.terrain import TerrainModel


def main() -> None:
    # Shared state
    state = SystemState()

    # Simple default target position (ahead of nominal trajectory).
    state.target_position = np.array([72.0, 0.0, 0.0], dtype=float)
    state.settings["drop_probability_threshold"] = 0.5

    # Single TerrainModel instance flows into BackgroundPlannerLoop for
    # ground-elevation lookup at target_x/target_y when building PropagationContext.
    terrain = TerrainModel()

    # Instantiate loops with requested rates.
    telemetry = TelemetryLoop(state, update_rate_hz=50.0)
    guidance = GuidanceLoop(state, update_rate_hz=12.0)
    ui = UIRenderLoop(state, update_rate_hz=30.0)
    planner = BackgroundPlannerLoop(state, update_rate_hz=1.0, terrain=terrain)

    loops = [telemetry, guidance, ui, planner]

    print("[Runtime] Starting SCYTHE loops. Press Ctrl+C to stop.")
    for loop in loops:
        loop.start()

    try:
        while state.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[Runtime] KeyboardInterrupt received, shutting down...")
        state.running = False

    # Give loops a moment to exit then join.
    for loop in loops:
        loop.join(timeout=2.0)

    print("[Runtime] All loops stopped. Goodbye.")


if __name__ == "__main__":
    main()

