"""SCYTHE Qt desktop entrypoint — unified GUI + backend loops.

GUI (PySide6) and backend runtime loops (Telemetry 50 Hz, Guidance 12 Hz,
BackgroundPlanner 1 Hz) are started from a single entry point. The loops
share the same SystemState instance as the GUI, so the Tactical Map
populates with live data.

UIRenderLoop is intentionally omitted — TacticalMapController already
runs a 30 Hz QTimer in the GUI thread, so a second UI loop would duplicate
work. _BaseLoop.start() spawns daemon threads internally, so loops die
when the Qt main loop exits.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from product.runtime.system_state import SystemState
from product.runtime.runtime_loops import (
    BackgroundPlannerLoop,
    GuidanceLoop,
    TelemetryLoop,
)
from product.terrain import TerrainModel


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SCYTHE")

    # Shared state — GUI and backend loops both read/write through this.
    state = SystemState()
    terrain = TerrainModel()

    # Daemon threads started via _BaseLoop.start(); terminate automatically
    # when QApplication.exec() returns (GUI closed).
    telemetry_loop = TelemetryLoop(state, update_rate_hz=50.0)
    guidance_loop = GuidanceLoop(state, update_rate_hz=12.0)
    planner_loop = BackgroundPlannerLoop(state, update_rate_hz=1.0, terrain=terrain)
    for loop in (telemetry_loop, guidance_loop, planner_loop):
        loop.start()

    window = MainWindow(state=state)
    window.show()
    try:
        return app.exec()
    finally:
        # Signal loops to exit their while-state.running loop; daemon=True
        # means the process will exit even if a tick is mid-flight.
        state.running = False


if __name__ == "__main__":
    raise SystemExit(main())
