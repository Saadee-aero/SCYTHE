from __future__ import annotations

from typing import Any, Optional, Tuple
import logging
import time
import math

import numpy as np

from PySide6.QtCore import QObject, QTimer

# TEMP: remove before v1.0 release
logger = logging.getLogger(__name__)

from product.runtime.system_state import SystemState
from product.sensors import CameraFeed
from product.system.tactical_map_state import TacticalMapState
from product.ui.tabs.tactical_map_tab import TacticalMapTab
from product.ui.widgets.status_banner import DropStatus, DropReason


class TacticalMapController(QObject):
    """Bridge SystemState to TacticalMapWidget at ~30 Hz."""

    def __init__(self, system_state: SystemState, tab: TacticalMapTab,
                 status_strip=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = system_state
        self._tab = tab
        self._widget = tab.map_widget
        self._status_strip = status_strip
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)
        self._last_tick = None
        self._last_impact_version = None
        self._frame_count = 0
        self._camera_feed = CameraFeed()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _on_tick(self) -> None:
        # TEMP: remove before v1.0 release
        t_start = time.perf_counter()
        now = time.monotonic()
        if self._last_tick is not None:
            interval = now - self._last_tick
            if interval > 0.05:
                dt_ms = interval * 1000.0
                print(f"[TacticalMap] UI lag detected | dt={dt_ms:.1f}ms")
        self._last_tick = now
        self._frame_count += 1
        if self._frame_count % 300 == 0:
            self._widget.normalize_transform()

        # TEMP: perf instrumentation
        _t_hud0 = time.perf_counter()
        # Camera feed: trace CameraFeed.get_frame() → widget.update_camera_feed() →
        # CameraFeedLayer.update_frame() at 30 Hz (TacticalMapController tick rate).
        vp = self._widget.viewport()
        frame = self._camera_feed.get_frame(vp.width(), vp.height())
        self._widget.update_camera_feed(frame)
        _t_hud1 = time.perf_counter()

        # TEMP: remove after profiling
        t0 = time.perf_counter()
        _t_eng0 = t0
        with self._state.lock:
            assert getattr(self._state, "monte_carlo_running", False) is False
            vehicle_state = getattr(self._state, "vehicle_state", None)
            target_position = getattr(self._state, "target_position", None)
            tactical_state = getattr(self._state, "tactical_map_state", None)
            envelope_result = getattr(self._state, "envelope_result", None)
            wind_vector = getattr(self._state, "wind_vector", None)
            impact_points = getattr(self._state, "impact_points", None)
            guidance_result = getattr(self._state, "guidance_result", None)
            wind_variance = getattr(self._state, "wind_variance", None)
            wind_variance_threshold = getattr(self._state, "wind_variance_threshold", 1.0)
            impact_version = getattr(self._state, "impact_data_version", None)
            hits = getattr(self._state, "hits", None)
            total_samples = getattr(self._state, "n_samples", None)
            p_hit = getattr(self._state, "P_hit", None)
            release_corridor = getattr(self._state, "release_corridor", None)
            if release_corridor is None and isinstance(tactical_state, TacticalMapState):
                release_corridor = getattr(tactical_state, "release_corridor", None)
            if release_corridor is None:
                release_corridor = self._get(envelope_result, "release_corridor", None)
            mission_committed = getattr(self._state, "mission_committed", False)

        # TEMP: remove after profiling
        t1 = time.perf_counter()

        vehicle_pos, vehicle_heading, vehicle_velocity = self._extract_vehicle(vehicle_state)
        if vehicle_pos is not None and vehicle_heading is not None:
            self._widget.update_vehicle_position(vehicle_pos[0], vehicle_pos[1], vehicle_heading)

        if target_position is not None:
            self._widget.update_target(target_position[0], target_position[1])

        if isinstance(tactical_state, TacticalMapState):
            self._apply_tactical_state(tactical_state, vehicle_pos)
            ub = tactical_state.uncertainty_breakdown or {}
            self._tab.update_uncertainty_bars(
                ub.get("wind", 0.0),
                ub.get("drag", 0.0),
                ub.get("release", 0.0),
                ub.get("vehicle", 0.0),
                ub.get("wind", 0.0),
                ub.get("drag", 0.0),
                ub.get("release", 0.0),
                ub.get("vehicle", 0.0),
            )
        else:
            self._apply_envelope_state(envelope_result, vehicle_pos, vehicle_velocity)

        if wind_vector is not None:
            self._widget.update_wind(wind_vector[0], wind_vector[1])
            # Trace: SystemState.wind_vector → controller → widget.update_wind_indicator
            # → WindIndicatorLayer.update_wind → paintEvent at 30 Hz.
            self._widget.update_wind_indicator(wind_vector[0], wind_vector[1])

        if impact_points is not None and impact_version is not None:
            if impact_version != self._last_impact_version:
                self._last_impact_version = impact_version
                self._widget.update_scatter(impact_points)
                self._widget.update_heatmap(impact_points)

        self._widget.update_guidance_arrow()

        status = self._get(guidance_result, "status", None)
        if status is not None:
            self._widget.update_status(status)

        drop_status, drop_reason = self._compute_banner_status(
            guidance_result, vehicle_state, target_position,
            wind_variance, wind_variance_threshold, mission_committed,
        )
        if self._status_strip is not None:
            self._status_strip.update_status(drop_status, drop_reason)

        if wind_variance is not None and wind_variance_threshold is not None:
            self._widget.update_wind_warning(float(wind_variance) > float(wind_variance_threshold))

        ci = None
        if hits is not None and total_samples:
            try:
                p_hit_val = float(hits) / float(total_samples)
                ci = math.sqrt(p_hit_val * (1.0 - p_hit_val) / float(total_samples))
            except Exception:
                p_hit_val = None
        else:
            p_hit_val = p_hit
        self._widget.update_p_hit(p_hit_val, ci)

        # Guidance + probability readouts into the strip's CENTER/RIGHT sections.
        # heading_error: radians (corridor_guidance.py) → degrees for display.
        # distance_to_corridor: meters.
        # CEP50: per §7.13 = 0.8326 * sqrt(λ1+λ2). tactical_state.ellipse_axes
        # stores semi-axes (a, b) = (sqrt(λ_major), sqrt(λ_minor)); so
        # λ1+λ2 = a² + b². Trivial-case check: isotropic σ → a=b=σ → CEP =
        # 0.8326·sqrt(2)·σ = 1.1774·σ (matches circular CEP50 formula).
        if self._status_strip is not None:
            if guidance_result is not None:
                he = self._get(guidance_result, "heading_error", None)
                dtc = self._get(guidance_result, "distance_to_corridor", None)
                heading_deg = math.degrees(float(he)) if he is not None else None
                dist_m = float(dtc) if dtc is not None else None
            else:
                heading_deg = None
                dist_m = None
            cep_m = None
            if isinstance(tactical_state, TacticalMapState):
                axes = getattr(tactical_state, "ellipse_axes", None)
                if axes:
                    try:
                        a = float(axes[0]); b = float(axes[1])
                        cep_m = 0.8326 * math.sqrt(max(a * a + b * b, 0.0))
                    except (TypeError, ValueError, IndexError):
                        cep_m = None
            self._status_strip.update_guidance(heading_deg, dist_m, p_hit_val, cep_m)

        # TEMP: remove after profiling
        t2 = time.perf_counter()
        _t_eng1 = t2
        _t_paint0 = t2

        t_drop = self._compute_drop_time(vehicle_state, release_corridor)
        self._widget.update_release_timer(t_drop)

        release_point = self._extract_release_point(guidance_result, envelope_result, tactical_state)
        impact_mean = self._extract_impact_mean(envelope_result, tactical_state)
        if release_point and impact_mean:
            self._widget.update_drift(release_point[0], release_point[1], impact_mean[0], impact_mean[1])
        else:
            self._widget.drift_arrow.set_visible(False)

        # TEMP: remove after profiling
        t3 = time.perf_counter()
        _t_paint1 = t3
        hud_ms = (_t_hud1 - _t_hud0) * 1000.0
        eng_ms = (_t_eng1 - _t_eng0) * 1000.0
        paint_ms = (_t_paint1 - _t_paint0) * 1000.0
        print(f"HUD={hud_ms:.1f}ms ENGINE={eng_ms:.1f}ms PAINT={paint_ms:.1f}ms")
        if (t3 - t0) * 1000.0 > 50.0:
            logger.warning(
                f"LAG BREAKDOWN: A={1000 * (t1 - t0):.1f}ms "
                f"B={1000 * (t2 - t1):.1f}ms "
                f"C={1000 * (t3 - t2):.1f}ms"
            )

        # TEMP: remove before v1.0 release
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        if elapsed_ms > 50:
            logger.warning(f"TICK EXCEEDED 50ms: {elapsed_ms:.1f}ms")
        else:
            logger.debug(f"tick: {elapsed_ms:.1f}ms")

    def _apply_tactical_state(self, state: TacticalMapState, vehicle_pos: Optional[Tuple[float, float]]) -> None:
        if state.impact_mean and state.ellipse_axes and state.ellipse_angle is not None:
            self._widget.update_impact_ellipse(
                state.impact_mean[0],
                state.impact_mean[1],
                state.ellipse_axes[0],
                state.ellipse_axes[1],
                state.ellipse_angle,
            )

        if state.release_corridor:
            self._widget.update_corridor(state.release_corridor)

        # Guidance arrow uses corridor centerline in widget.

    def _apply_envelope_state(
        self,
        envelope_result: Any,
        vehicle_pos: Optional[Tuple[float, float]],
        vehicle_velocity: Any = None,
    ) -> None:
        if envelope_result is None:
            return
        impact_mean = self._get(envelope_result, "impact_mean", None)
        impact_cov = self._get(envelope_result, "impact_cov", None)
        feasible_offsets = self._get(envelope_result, "feasible_offsets", None)

        if impact_mean is not None and impact_cov is not None:
            try:
                mean_arr = np.asarray(impact_mean, dtype=float).flatten()[:2]
                cov_arr = np.asarray(impact_cov, dtype=float).reshape(2, 2)
                # eigh returns eigenvalues ascending; [1]=major, [0]=minor
                eigvals, eigvecs = np.linalg.eigh(cov_arr)
                a = math.sqrt(max(float(eigvals[1]), 0.0))
                b = math.sqrt(max(float(eigvals[0]), 0.0))
                angle = math.degrees(math.atan2(float(eigvecs[1, 1]), float(eigvecs[0, 1])))
                self._widget.update_impact_ellipse(
                    float(mean_arr[0]), float(mean_arr[1]), a, b, angle
                )
                # CEP50 per §7.13: 0.8326 * sqrt(lambda1 + lambda2) using
                # eigenvalues directly (not semi-axes). Trivial check:
                # isotropic sigma -> eigvals=(s^2, s^2) -> CEP = 0.8326*sqrt(2)*s
                # which matches the CEP50 = 1.1774*sigma formula for circular CEP.
                cep50_m = 0.8326 * math.sqrt(
                    max(float(eigvals[0]), 0.0) + max(float(eigvals[1]), 0.0)
                )
                self._widget.update_cep_label(cep50_m)
            except Exception:
                pass

        if feasible_offsets and vehicle_pos is not None and vehicle_velocity is not None:
            try:
                offsets = np.asarray(list(feasible_offsets), dtype=float)
                if offsets.size:
                    vel_arr = np.asarray(vehicle_velocity, dtype=float).flatten()
                    vel_xy = vel_arr[:2]
                    norm = float(np.linalg.norm(vel_xy))
                    if norm > 1e-6:
                        fwd = vel_xy / norm
                        lat = np.array([-fwd[1], fwd[0]], dtype=float)
                        hw = float(np.max(np.abs(offsets)))
                        cx, cy = float(vehicle_pos[0]), float(vehicle_pos[1])
                        half_len = 50.0  # corridor half-length in world meters
                        corners = [
                            (cx - fwd[0] * half_len + lat[0] * hw,
                             cy - fwd[1] * half_len + lat[1] * hw),
                            (cx + fwd[0] * half_len + lat[0] * hw,
                             cy + fwd[1] * half_len + lat[1] * hw),
                            (cx + fwd[0] * half_len - lat[0] * hw,
                             cy + fwd[1] * half_len - lat[1] * hw),
                            (cx - fwd[0] * half_len - lat[0] * hw,
                             cy - fwd[1] * half_len - lat[1] * hw),
                        ]
                        self._widget.update_corridor(corners)
            except Exception:
                pass

        # Guidance arrow uses corridor centerline in widget.

    @staticmethod
    def _extract_vehicle(vehicle_state: Any) -> Tuple[Optional[Tuple[float, float]], Optional[float], Optional[Tuple[float, float]]]:
        if vehicle_state is None:
            return None, None, None
        pos = TacticalMapController._get(vehicle_state, "position", None)
        heading = TacticalMapController._get(vehicle_state, "heading", None)
        if heading is None:
            heading = TacticalMapController._get(vehicle_state, "heading_deg", None)
        velocity = TacticalMapController._get(vehicle_state, "velocity", None)
        if pos is None:
            return None, heading, velocity
        return (float(pos[0]), float(pos[1])), heading, velocity

    @staticmethod
    def _compute_drop_time(vehicle_state: Any, corridor: Any) -> Optional[float]:
        pos = TacticalMapController._get(vehicle_state, "position", None)
        vel = TacticalMapController._get(vehicle_state, "velocity", None)
        if pos is None or vel is None or corridor is None:
            return None
        try:
            vx, vy = float(vel[0]), float(vel[1])
            speed = math.hypot(vx, vy)
            if speed <= 0:
                return None
            pts = list(corridor)
            if len(pts) < 2:
                return None
            entry_x = (pts[0][0] + pts[1][0]) * 0.5
            entry_y = (pts[0][1] + pts[1][1]) * 0.5
            dist = math.hypot(entry_x - pos[0], entry_y - pos[1])
            return dist / speed
        except Exception:
            return None

    @staticmethod
    def _extract_release_point(guidance_result: Any, envelope_result: Any, tactical_state: Any) -> Optional[Tuple[float, float]]:
        rp = TacticalMapController._get(guidance_result, "target_release_point", None)
        if rp is None:
            rp = TacticalMapController._get(envelope_result, "release_point", None)
        if rp is None and isinstance(tactical_state, TacticalMapState):
            rp = TacticalMapController._get(tactical_state, "release_point", None)
        if rp is None:
            return None
        return (float(rp[0]), float(rp[1]))

    @staticmethod
    def _extract_impact_mean(envelope_result: Any, tactical_state: Any) -> Optional[Tuple[float, float]]:
        im = TacticalMapController._get(envelope_result, "impact_mean", None)
        if im is None and isinstance(tactical_state, TacticalMapState):
            im = TacticalMapController._get(tactical_state, "impact_mean", None)
        if im is None:
            return None
        return (float(im[0]), float(im[1]))

    @staticmethod
    def _compute_banner_status(
        guidance_result: Any,
        vehicle_state: Any,
        target_position: Any,
        wind_variance: Any,
        wind_variance_threshold: Any,
        mission_committed: bool,
    ) -> tuple:
        if not mission_committed:
            return DropStatus.NO_DROP, DropReason.MISSION_PARAMS_NOT_SET
        if guidance_result is None or vehicle_state is None or target_position is None:
            return DropStatus.NO_DROP, DropReason.UAV_TOO_FAR
        status_str = (TacticalMapController._get(guidance_result, "status", "") or "").strip().upper()
        if status_str == "DROP_NOW":
            return DropStatus.DROP_NOW, DropReason.NONE
        if status_str == "IN_DROP_ZONE":
            return DropStatus.IN_DROP_ZONE, DropReason.NONE
        if status_str == "APPROACH_CORRIDOR":
            return DropStatus.APPROACH_CORRIDOR, DropReason.UAV_TOO_FAR
        # NO_DROP — determine reason
        if wind_variance is not None and wind_variance_threshold is not None:
            try:
                if float(wind_variance) > float(wind_variance_threshold):
                    return DropStatus.NO_DROP, DropReason.WIND_EXCEEDED
            except (TypeError, ValueError):
                pass
        return DropStatus.NO_DROP, DropReason.NONE

    @staticmethod
    def _get(obj: Any, name: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
