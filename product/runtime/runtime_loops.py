"""
Runtime loop implementations for SCYTHE.

Each loop runs in its own thread at a fixed update rate and reads/writes
shared `SystemState` to connect telemetry, planner, guidance, and UI.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import sys
from pathlib import Path

# Ensure project root is on sys.path so 'product' is importable
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import math
import threading
import time
from typing import Optional

import numpy as np

from product.aircraft import VehicleState
from product.aircraft.motion_predictor import MotionPredictor
from product.explorer import compute_release_envelope
from product.guidance.corridor_guidance import compute_corridor_guidance
from product.physics.propagation_context import build_propagation_context
from product.runtime.system_state import SystemState
from product.terrain import TerrainModel


class _BaseLoop:
    """Base class for simple fixed-rate loops."""

    def __init__(self, system_state: SystemState, update_rate_hz: float, name: str):
        self._state = system_state
        self._period = 1.0 / float(update_rate_hz)
        self._name = name
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name=self._name, daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run_loop(self) -> None:
        while self._state.running:
            start = time.perf_counter()
            try:
                self.tick()
            except Exception as exc:  # pragma: no cover - defensive logging
                print(f"[{self._name}] Exception in loop: {exc}")
            elapsed = time.perf_counter() - start
            sleep_time = max(0.0, self._period - elapsed)
            time.sleep(sleep_time)

    def tick(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class TelemetryLoop(_BaseLoop):
    """
    Simulated telemetry source.

    For now this synthesizes a simple 2D trajectory:
    - 0–10 s: straight flight along +x.
    - 10+ s: gentle coordinated turn.
    """

    def __init__(self, system_state: SystemState, update_rate_hz: float = 50.0):
        super().__init__(system_state, update_rate_hz, name="TelemetryLoop")
        self._t = 0.0
        self._last_planned_position: Optional[np.ndarray] = None

    def tick(self) -> None:
        dt = self._period
        self._t += dt

        # Simple kinematic model.
        z = 200.0
        speed = 25.0
        if self._t < 10.0:
            heading = 0.0
            ax = 0.0
            ay = 0.0
        else:
            # Constant yaw rate turn.
            omega = 0.12
            heading = omega * (self._t - 10.0)
            ax = -speed * omega * math.sin(heading)
            ay = speed * omega * math.cos(heading)

        vx = speed * math.cos(heading)
        vy = speed * math.sin(heading)

        # Integrate position with simple Euler.
        with self._state.lock:
            vs_prev = self._state.vehicle_state
        if vs_prev is None:
            x, y = 0.0, 0.0
        else:
            x = float(vs_prev.position[0] + vs_prev.velocity[0] * dt)
            y = float(vs_prev.position[1] + vs_prev.velocity[1] * dt)

        pos = np.array([x, y, z], dtype=float)
        vel = np.array([vx, vy, 0.0], dtype=float)
        acc = np.array([ax, ay, 0.0], dtype=float)

        with self._state.lock:
            self._state.vehicle_state = VehicleState(
                position=pos,
                velocity=vel,
                acceleration=acc,
                timestamp=self._t,
            )
            # Mark envelope dirty if UAV has moved more than 2.0 meters.
            if self._last_planned_position is not None:
                distance = float(np.linalg.norm(pos[:2] - self._last_planned_position[:2]))
                if distance > 2.0:
                    self._state.envelope_dirty = True
                    self._last_planned_position = pos.copy()
            elif self._last_planned_position is None:
                self._last_planned_position = pos.copy()


class GuidanceLoop(_BaseLoop):
    """Computes corridor guidance from current envelope and vehicle state."""

    def __init__(self, system_state: SystemState, update_rate_hz: float = 12.0):
        super().__init__(system_state, update_rate_hz, name="GuidanceLoop")

    def tick(self) -> None:
        with self._state.lock:
            vs = self._state.vehicle_state
            envelope = self._state.envelope_result
        if vs is None or envelope is None:
            return

        # Threshold can be overridden via settings, default 0.5.
        threshold = float(self._state.settings.get("drop_probability_threshold", 0.5))
        
        # Predict UAV state 500ms ahead for better guidance accuracy.
        predictor = MotionPredictor(vs)
        pos_pred, vel_pred = predictor.predict_state(vs.timestamp + 0.5)
        
        gr = compute_corridor_guidance(
            envelope_result=envelope,
            pos_uav=pos_pred,
            vel_uav=vel_pred,
            current_time=vs.timestamp,
            threshold=threshold,
        )
        with self._state.lock:
            self._state.guidance_result = gr


class BackgroundPlannerLoop(_BaseLoop):
    """
    Runs release-envelope solver in the background at ~1 Hz.

    Uses a fixed propagation context; sees updated initial conditions via
    vehicle_state and target_position.
    """

    def __init__(
        self,
        system_state: SystemState,
        update_rate_hz: float = 1.0,
        terrain: Optional[TerrainModel] = None,
    ):
        super().__init__(system_state, update_rate_hz, name="BackgroundPlannerLoop")
        self._terrain = terrain if terrain is not None else TerrainModel()

        # Minimal config-like object with required attributes.
        # Use coarse grid for real-time (~15s per envelope):
        # 3 offsets, ~8 time steps per offset.
        class _Cfg:
            max_lateral_offset = 8.0
            offset_step = 8.0
            drop_probability_threshold = 0.5
            compute_heatmap = False
            max_release_time = 2.5
            release_time_step = 0.25
            target_radius = 15.0
            # Actuator delay 0.3s: accounts for release mechanism
            # servo + trigger latency at operational speeds
            release_delay = 0.3
            wind_sigma0 = 0.8
            wind_sigma_altitude_coeff = 0.001
            wind_sigma_max = 4.0
            release_pos_sigma = 0.5
            velocity_sigma = 0.02
            enable_hybrid_estimation = False
            wind_std = 0.8
            random_seed = 42
            n_samples = 500
            max_mc_verifications = 10

        self._config = _Cfg()
        self._busy = False

    def tick(self) -> None:
        if self._busy:
            return
        with self._state.lock:
            vs = self._state.vehicle_state
            target = self._state.target_position
            dirty = self._state.envelope_dirty
        if vs is None or target is None:
            return
        if not dirty:
            return

        self._busy = True
        try:
            # Read physics values from settings with fallbacks
            settings = self._state.settings
            mass = float(settings.get("mass", 1.0))
            Cd = float(settings.get("Cd", 1.0))
            area = float(settings.get("area", 0.01))
            wind_mean = np.array(settings.get("wind_mean", [2.0, 0.0, 0.0]), dtype=float)
            dt = 0.05
            target_pos = np.asarray(target, dtype=float).reshape(-1)
            ground_z = float(self._terrain.get_elevation(float(target_pos[0]), float(target_pos[1])))
            context = build_propagation_context(
                mass=mass,
                Cd=Cd,
                area=area,
                wind_ref=wind_mean.reshape(3),
                shear=None,
                target_z=ground_z,
                dt=dt,
            )
            logger.debug("BackgroundPlannerLoop: computing release envelope")
            with _suppress_timing():
                env = compute_release_envelope(
                    context,
                    self._config,
                    vs.position,
                    vs.velocity,
                    target_pos,
                )
            with self._state.lock:
                self._state.envelope_result = env
                self._state.envelope_dirty = False
            logger.debug("BackgroundPlannerLoop: envelope ready")
        finally:
            self._busy = False


class UIRenderLoop(_BaseLoop):
    """Simple debug UI loop that prints state to the console."""

    def __init__(self, system_state: SystemState, update_rate_hz: float = 30.0):
        super().__init__(system_state, update_rate_hz, name="UIRenderLoop")
        self._last_print_time = 0.0

    def tick(self) -> None:
        with self._state.lock:
            vs = self._state.vehicle_state
            gr = self._state.guidance_result
        if vs is None:
            return

        now = time.perf_counter()
        if now - self._last_print_time < 1.0:
            return
        self._last_print_time = now

        status = gr.status if gr is not None else "NO_DATA"
        t_rel = gr.time_to_release if gr is not None else 0.0
        print(
            f"[UI] t={vs.timestamp:5.2f}s  pos=({vs.position[0]:6.1f}, {vs.position[1]:6.1f}, {vs.position[2]:5.1f})  "
            f"status={status:12s}  t_release={t_rel:5.2f}s"
        )


def _suppress_timing():
    """Context manager to silence internal timing prints from Monte Carlo engine."""
    import contextlib
    import io
    import sys

    @contextlib.contextmanager
    def _quiet():
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            yield
        finally:
            sys.stdout = old

    return _quiet()

