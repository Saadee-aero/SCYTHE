"""
Qt-based desktop application entrypoint for AIRDROP-X.

This wraps the existing engine and Matplotlib-based tab renderers in a
PyQt6 window with a tab widget. The engine and decision logic remain
unchanged; this module is UI-only.
"""

from __future__ import annotations

import sys
import random
from datetime import datetime
from typing import Dict, Any, Optional

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QMessageBox,
)

from configs import mission_configs as cfg
from src import metrics
from product.payloads.payload_base import Payload
from product.missions.target_manager import Target
from product.missions.environment import Environment
from product.missions.mission_state import MissionState
from product.guidance.advisory_layer import (
    get_impact_points_and_metrics,
    evaluate_advisory,
)
from product.ui import qt_bridge
from product.ui.tabs import mission_overview, payload_library, sensor_telemetry, analysis, system_status
from product.integrations.state_buffer import StateBuffer
from product.integrations.telemetry_contract import TelemetryFrame
from product.integrations.telemetry_health import check_telemetry_health


def run_simulation_from_config(
    random_seed: int,
    telemetry_frame: Optional[TelemetryFrame] = None,
) -> Dict[str, Any]:
    """
    Run one engine evaluation and return a simulation snapshot.

    The engine and decision logic are treated as a black box. This helper
    only wires config into the engine and packages the results.

    Parameters
    ----------
    random_seed : int
        Random seed for Monte Carlo reproducibility.
    telemetry_frame : TelemetryFrame, optional
        If provided, use telemetry position and velocity instead of config
        defaults. If None, fall back to cfg.uav_pos and cfg.uav_vel.
    """
    payload = Payload(
        mass=cfg.mass,
        drag_coefficient=cfg.Cd,
        reference_area=cfg.A,
    )
    target = Target(position=cfg.target_pos, radius=cfg.target_radius)
    environment = Environment(
        wind_mean=cfg.wind_mean,
        wind_std=cfg.wind_std,
    )

    # Use telemetry if available; otherwise fall back to config defaults.
    if telemetry_frame is not None:
        uav_pos = telemetry_frame.position
        uav_vel = telemetry_frame.velocity
    else:
        uav_pos = cfg.uav_pos
        uav_vel = cfg.uav_vel

    mission_state = MissionState(
        payload=payload,
        target=target,
        environment=environment,
        uav_position=uav_pos,
        uav_velocity=uav_vel,
    )
    # Engine call: Monte Carlo and metrics.
    (
        impact_points,
        P_hit,
        cep50,
        impact_velocity_stats,
    ) = get_impact_points_and_metrics(
        mission_state,
        random_seed,
        {"n_samples": cfg.n_samples},
    )
    base_snapshot = {
        "impact_points": impact_points,
        "P_hit": P_hit,
        "cep50": cep50,
        "target_position": mission_state.target.position,
        "target_radius": mission_state.target.radius,
    }
    advisory_result = evaluate_advisory(
        base_snapshot,
        cfg.THRESHOLD_SLIDER_INIT / 100.0,
    )
    m = mission_state.payload.mass
    cd = mission_state.payload.drag_coefficient
    area = mission_state.payload.reference_area
    bc = (m / (cd * area)) if (cd and area) else None
    telemetry_freshness = 0.0 if telemetry_frame is not None else None
    confidence_index = metrics.compute_confidence_index(
        wind_std=cfg.wind_std,
        ballistic_coefficient=bc,
        altitude=uav_pos[2],
        telemetry_freshness=telemetry_freshness,
    )

    # Immutable snapshot: config + results + timestamp.
    config: Dict[str, Any] = {
        "random_seed": random_seed,
        "n_samples": cfg.n_samples,
        "dt": cfg.dt,
        "mass": cfg.mass,
        "Cd": cfg.Cd,
        "A": cfg.A,
        "uav_pos": uav_pos,  # May be from telemetry or config default.
        "uav_vel": uav_vel,  # May be from telemetry or config default.
        "target_pos": cfg.target_pos,
        "target_radius": cfg.target_radius,
        "wind_mean": cfg.wind_mean,
        "wind_std": cfg.wind_std,
        "mode_thresholds": cfg.MODE_THRESHOLDS,
        "telemetry_source": (
            telemetry_frame.source if telemetry_frame else "config_default"
        ),
    }

    results: Dict[str, Any] = {
        "impact_points": impact_points,
        "P_hit": P_hit,
        "cep50": cep50,
        "impact_velocity_stats": impact_velocity_stats,
        "max_safe_impact_speed": None,
        "target_position": mission_state.target.position,
        "target_radius": mission_state.target.radius,
        "mission_state": mission_state,
        "advisory_result": advisory_result,
        "confidence_index": confidence_index,
        "initial_threshold_percent": cfg.THRESHOLD_SLIDER_INIT,
        "initial_mode": "Balanced",
        "slider_min": cfg.THRESHOLD_SLIDER_MIN,
        "slider_max": cfg.THRESHOLD_SLIDER_MAX,
        "slider_step": cfg.THRESHOLD_SLIDER_STEP,
        "mode_thresholds": cfg.MODE_THRESHOLDS,
    }

    snapshot: Dict[str, Any] = {
        "config": config,
        "results": results,
        "created_at": datetime.now(),
    }
    return snapshot


class AirdropMainWindow(QMainWindow):
    """Qt main window hosting the AIRDROP-X tabs."""

    def __init__(self, snapshot: Dict[str, Any], telemetry_buffer: Optional[StateBuffer] = None) -> None:
        super().__init__()
        self.setWindowTitle("AIRDROP-X")

        # Current immutable simulation snapshot driving all tabs.
        self._snapshot: Dict[str, Any] = snapshot

        # Telemetry buffer (optional; if None, simulation uses config defaults).
        self._telemetry_buffer: Optional[StateBuffer] = telemetry_buffer

        # Seed regeneration mode (explicitly toggled by operator).
        self._regen_seed_mode: bool = False

        # Standard/Advanced display mode.
        # Default mode at startup must be standard.
        self.current_mode: str = "standard"

        # UI references
        self._tabs: QTabWidget | None = None
        self._snapshot_label: QLabel | None = None
        self._status_seed_label: QLabel | None = None
        self._regen_seed_checkbox: QCheckBox | None = None
        self._operator_mode_btn: QPushButton | None = None
        self._engineering_mode_btn: QPushButton | None = None

        self._init_ui()

    # Convenience accessors
    @property
    def _cfg(self) -> Dict[str, Any]:
        return self._snapshot["config"]

    @property
    def _results(self) -> Dict[str, Any]:
        return self._snapshot["results"]

    def _init_ui(self) -> None:
        """Build static window chrome plus the tab widget."""
        central = QWidget(self)
        vlayout = QVBoxLayout(central)
        vlayout.setContentsMargins(4, 4, 4, 4)

        # Snapshot banner + explicit re-run control.
        header = QWidget(central)
        hlayout = QHBoxLayout(header)
        hlayout.setContentsMargins(0, 0, 0, 0)

        self._snapshot_label = QLabel(header)
        self._operator_mode_btn = QPushButton("Standard", header)
        self._engineering_mode_btn = QPushButton("Advanced", header)
        self._operator_mode_btn.setCheckable(True)
        self._engineering_mode_btn.setCheckable(True)
        self._operator_mode_btn.clicked.connect(
            self._on_operator_mode_clicked
        )
        self._engineering_mode_btn.clicked.connect(
            self._on_engineering_mode_clicked
        )
        rerun_btn = QPushButton("Re-Run Simulation", header)
        rerun_btn.clicked.connect(self._on_rerun_clicked)

        hlayout.addWidget(self._snapshot_label)
        hlayout.addStretch(1)
        hlayout.addWidget(self._operator_mode_btn)
        hlayout.addWidget(self._engineering_mode_btn)
        hlayout.addWidget(rerun_btn)

        self._tabs = QTabWidget(central)
        self._build_tabs()
        # Keep snapshot banner and operator context in sync with tab changes.
        self._tabs.currentChanged.connect(self._on_tab_changed)

        vlayout.addWidget(header)
        vlayout.addWidget(self._tabs)

        self.setCentralWidget(central)
        self.update_mode_styles()
        self._update_snapshot_banner()

    # ---- Snapshot lifecycle ----

    def _build_tabs(self) -> None:
        """(Re)build all tabs from the current snapshot."""
        assert self._tabs is not None
        self._tabs.clear()
        self._tabs.addTab(
            self._make_mission_overview_tab(), "Mission Overview"
        )
        self._tabs.addTab(self._make_payload_tab(), "Payload Library")
        self._tabs.addTab(self._make_sensor_tab(), "Sensor & Telemetry")
        self._tabs.addTab(self._make_analysis_tab(), "Analysis")
        self._tabs.addTab(self._make_system_status_tab(), "System Status")

    def _update_snapshot_banner(self) -> None:
        """Update header label describing the current snapshot."""
        if self._snapshot_label is None:
            return
        created = self._snapshot["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        seed = self._cfg["random_seed"]
        mode = "non-reproducible" if self._regen_seed_mode else "reproducible"
        # Show which tab is active so the operator always knows context.
        active_tab = ""
        if self._tabs is not None and self._tabs.count() > 0:
            idx = self._tabs.currentIndex()
            if idx >= 0:
                active_tab = self._tabs.tabText(idx)
        banner = f"Simulation snapshot @ {created}  ·  seed={seed} ({mode})"
        if active_tab:
            banner += f"  ·  Active tab: {active_tab}"
        self._snapshot_label.setText(banner)
        if self._status_seed_label is not None:
            self._status_seed_label.setText(
                f"Seed used for this snapshot: {seed}"
            )

    def _on_tab_changed(self, index: int) -> None:
        """Refresh header when operator switches tabs (context clarity)."""
        # index is not used directly; we recompute from current tab state.
        self._update_snapshot_banner()

    def update_mode_styles(self) -> None:
        """Apply active/inactive visual state for Standard/Advanced mode."""
        if self._operator_mode_btn is None or self._engineering_mode_btn is None:
            return

        active_style = (
            "QPushButton {"
            " color: #2CFF05;"
            " border: 2px solid #2CFF05;"
            " background-color: rgba(44,255,5,0.08);"
            " font-weight: bold;"
            "}"
        )
        inactive_style = (
            "QPushButton {"
            " color: #6C8F6A;"
            " border: 1px solid #1A2A1A;"
            " background-color: transparent;"
            " font-weight: normal;"
            "}"
        )

        is_standard = self.current_mode == "standard"
        self._operator_mode_btn.setChecked(is_standard)
        self._engineering_mode_btn.setChecked(not is_standard)

        self._operator_mode_btn.setStyleSheet(
            active_style if is_operator else inactive_style
        )
        self._engineering_mode_btn.setStyleSheet(
            inactive_style if is_operator else active_style
        )

    def _on_operator_mode_clicked(self) -> None:
        if self.current_mode == "standard":
            self.update_mode_styles()
            return
        self.current_mode = "standard"
        current_idx = self._tabs.currentIndex() if self._tabs is not None else 0
        self._build_tabs()
        if self._tabs is not None and 0 <= current_idx < self._tabs.count():
            self._tabs.setCurrentIndex(current_idx)
        self.update_mode_styles()

    def _on_engineering_mode_clicked(self) -> None:
        if self.current_mode == "advanced":
            self.update_mode_styles()
            return
        self.current_mode = "advanced"
        current_idx = self._tabs.currentIndex() if self._tabs is not None else 0
        self._build_tabs()
        if self._tabs is not None and 0 <= current_idx < self._tabs.count():
            self._tabs.setCurrentIndex(current_idx)
        self.update_mode_styles()

    def _on_rerun_clicked(self) -> None:
        """
        Explicitly re-run the engine and replace the snapshot.

        If telemetry is available from StateBuffer, inject it into the
        simulation configuration. If telemetry is stale or missing, show
        a warning but proceed with config defaults (do not crash).
        """
        current_seed = int(self._cfg["random_seed"])
        seed = current_seed
        if self._regen_seed_mode:
            # Non-reproducible mode: draw a fresh seed for this snapshot.
            seed = random.randint(0, 2**31 - 1)
            print(f"[AIRDROP-X] New non-reproducible seed generated: {seed}")

        # Attempt to read latest telemetry from StateBuffer.
        telemetry_frame: Optional[TelemetryFrame] = None
        if self._telemetry_buffer is not None:
            telemetry_frame = self._telemetry_buffer.get_latest()
            if telemetry_frame is None:
                QMessageBox.warning(
                    self,
                    "No Telemetry Available",
                    "No telemetry frame found in StateBuffer. Using config "
                    "defaults for UAV position and velocity.",
                )
            elif self._telemetry_buffer.is_stale(max_age_seconds=5.0):
                QMessageBox.warning(
                    self,
                    "Stale Telemetry",
                    f"Latest telemetry frame is stale (>5 seconds old). Using "
                    f"config defaults for UAV position and velocity.\n\n"
                    f"Telemetry source: {telemetry_frame.source}\n"
                    f"Telemetry timestamp: {telemetry_frame.timestamp:.2f} s",
                )
                telemetry_frame = None  # Ignore stale telemetry.

        # New immutable snapshot from the engine (with or without telemetry).
        self._snapshot = run_simulation_from_config(
            seed, telemetry_frame=telemetry_frame
        )
        # Rebuild all tabs from the new snapshot.
        self._build_tabs()
        self._update_snapshot_banner()

    def _on_regen_seed_toggled(self, checked: bool) -> None:
        """Operator explicitly toggles non-reproducible seed mode."""
        self._regen_seed_mode = checked
        self._update_snapshot_banner()

    # ---- Tab factories ----

    def _wrap_canvas(self, fig) -> QWidget:
        canvas = qt_bridge.create_canvas(fig)
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        return widget

    def _make_mission_overview_tab(self) -> QWidget:
        fig = qt_bridge.create_figure()
        rp = (
            self._results["mission_state"].uav_position[:2]
            if self._results.get("mission_state")
            else None
        )
        mission_data = {
            "decision": self._results["advisory_result"].current_feasibility,
            "target_hit_percentage": self._results["P_hit"] * 100.0,
            "cep50": self._results["cep50"],
            "threshold": self._results["initial_threshold_percent"],
            "mode": self._results["initial_mode"],
            "impact_points": self._results["impact_points"],
            "target_position": self._results["target_position"],
            "target_radius": self._results["target_radius"],
            "confidence_index": self._results.get("confidence_index"),
            "release_point": rp,
            "wind_vector": self._cfg.get("wind_mean", (0.0, 0.0))[:2],
        }
        qt_bridge.render_into_single_axes(
            fig, mission_overview.render, **mission_data
        )
        return self._wrap_canvas(fig)

    def _make_payload_tab(self) -> QWidget:
        """
        Payload Library is view-only in the Qt app.

        Controls in this tab do NOT trigger a new engine run, so we render in
        non-interactive mode and mark this as locked for the current snapshot.

        Use same figsize and content rect as main.py so layout is symmetrical.
        """
        fig = qt_bridge.create_figure(figsize=(10.0, 6.0))
        ax = fig.add_axes([0.06, 0.06, 0.88, 0.78])
        payload_library.render(ax, fig, interactive=False)
        return self._wrap_canvas(fig)

    def _make_sensor_tab(self) -> QWidget:
        fig = qt_bridge.create_figure()
        qt_bridge.render_into_single_axes(
            fig,
            sensor_telemetry.render,
            wind_mean_ms=float(self._cfg.get("wind_mean", (0.0, 0.0, 0.0))[0]),
            wind_std_dev_ms=self._cfg.get("wind_std"),
            telemetry_live=False,
        )
        return self._wrap_canvas(fig)

    def _make_analysis_tab(self) -> QWidget:
        fig = qt_bridge.create_figure()
        uav_pos = (
            self._results["mission_state"].uav_position
            if self._results.get("mission_state")
            else None
        )
        analysis_kwargs = {
            "impact_points": self._results["impact_points"],
            "target_position": self._results["target_position"],
            "target_radius": self._results["target_radius"],
            "uav_position": uav_pos,
            "wind_mean": self._cfg.get("wind_mean"),
            "cep50": self._results["cep50"],
            "target_hit_percentage": self._results["P_hit"] * 100.0,
            "impact_velocity_stats": self._results.get(
                "impact_velocity_stats"
            ),
            "max_safe_impact_speed": self._results.get(
                "max_safe_impact_speed"
            ),
            "dispersion_mode": self.current_mode,
        }
        qt_bridge.render_into_single_axes(
            fig, analysis.render, **analysis_kwargs
        )
        return self._wrap_canvas(fig)

    def _make_system_status_tab(self) -> QWidget:
        """
        System Status tab: audit-grade identity and reproducibility info.

        Includes a visible seed banner and a control to switch between
        reproducible and non-reproducible seed behaviour.
        """
        # Perform telemetry health checks (advisory only).
        health_warnings = check_telemetry_health(
            self._telemetry_buffer,
            stale_threshold_seconds=5.0,
            min_update_rate_hz=1.0,
        )

        fig = qt_bridge.create_figure()
        status_kwargs = {
            "random_seed": self._cfg.get("random_seed"),
            "n_samples": self._cfg.get("n_samples"),
            "dt": self._cfg.get("dt"),
        }
        # Pass snapshot timestamp down so System Status can show creation time.
        status_kwargs["snapshot_created_at"] = self._snapshot["created_at"]
        # Merge telemetry health warnings into System Status warnings.
        existing_warnings = self._snapshot.get("warnings", [])
        if health_warnings:
            # Combine: existing warnings + telemetry health warnings.
            all_warnings = existing_warnings + health_warnings
        else:
            all_warnings = (
                existing_warnings
                if existing_warnings
                else ["No active warnings."]
            )
        status_kwargs["warnings"] = all_warnings
        qt_bridge.render_into_single_axes(
            fig, system_status.render, **status_kwargs
        )

        canvas = qt_bridge.create_canvas(fig)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Seed banner + regenerate control (operator-visible).
        seed_banner = QLabel(container)
        self._status_seed_label = seed_banner
        self._update_snapshot_banner()

        regen_box = QCheckBox("Regenerate seed (non-reproducible)", container)
        regen_box.setChecked(False)
        regen_box.toggled.connect(self._on_regen_seed_toggled)
        self._regen_seed_checkbox = regen_box

        layout.addWidget(seed_banner)
        layout.addWidget(regen_box)

        # Brief seed mode explanation for auditors / operators.
        mode_explain = QLabel(
            "Seed policy: unchecked = reproducible (same seed each re-run); "
            "checked = new random seed for each re-run (non-reproducible).",
            container,
        )
        layout.addWidget(mode_explain)
        layout.addWidget(canvas)

        return container


# Military HUD styling: dark background, green accent, monospace
_HUD_STYLESHEET = """
    QMainWindow, QWidget { background-color: #0c0e0c; }
    QLabel { color: #c0d0c0; font-family: Consolas, monospace; font-size: 9pt; }
    QPushButton {
        background-color: #141814;
        color: #00ff41;
        border: 1px solid #2a3a2a;
        font-family: Consolas, monospace;
        font-size: 9pt;
        padding: 4px 10px;
    }
    QPushButton:hover { background-color: #1e221e; border-color: #00ff41; }
    QPushButton:pressed { background-color: #0f120f; }
    QTabWidget::pane {
        border: 1px solid #2a3a2a;
        top: -1px;
        background-color: #0c0e0c;
    }
    QTabBar::tab {
        background-color: #141814;
        color: #6b8e6b;
        border: 1px solid #2a3a2a;
        border-bottom: none;
        padding: 6px 14px;
        margin-right: 2px;
        font-family: Consolas, monospace;
        font-size: 9pt;
    }
    QTabBar::tab:selected { color: #00ff41; background-color: #0c0e0c; }
    QTabBar::tab:hover:!selected { color: #90e090; }
    QCheckBox { color: #c0d0c0; font-family: Consolas, monospace; font-size: 9pt; }
    QCheckBox::indicator { border: 1px solid #2a3a2a; background-color: #141814; }
    QCheckBox::indicator:checked { background-color: #00ff41; }
"""


def main(telemetry_buffer: Optional[StateBuffer] = None) -> None:
    """
    Entry point for the Qt desktop application.

    Parameters
    ----------
    telemetry_buffer : StateBuffer, optional
        If provided, the app will attempt to read telemetry from this buffer
        when the operator clicks "Re-Run Simulation". If None, all simulations
        use config defaults for UAV position and velocity.
    """
    # Default is fully reproducible: use config seed unless operator chooses otherwise.
    initial_seed = cfg.RANDOM_SEED
    snapshot = run_simulation_from_config(initial_seed, telemetry_frame=None)

    app = QApplication(sys.argv)
    app.setStyleSheet(_HUD_STYLESHEET)
    window = AirdropMainWindow(snapshot, telemetry_buffer=telemetry_buffer)
    window.resize(1200, 720)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

