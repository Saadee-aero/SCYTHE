"""SCYTHE Phase 1 PySide6 main window shell."""

from __future__ import annotations

from datetime import datetime
import time
from enum import Enum

from PySide6.QtCore import QEvent, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QCursor, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLineEdit,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from adapter import run_simulation_snapshot
from color_profile import adjust_color_intensity
from evaluation_worker import EvaluationWorker, TelemetryState, ConfigState
from snapshot_validation import validate_snapshot
from src.decision_stability import enrich_evaluation_snapshot
from mission_config_tab import MissionConfigTab
from telemetry import TelemetryWorker
from widgets import NoWheelDoubleSpinBox, NoWheelSlider, StatusStrip
from product.ui import qt_bridge
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from product.runtime.system_state import SystemState
from product.ui.tactical_map_controller import TacticalMapController
from product.ui.tabs.tactical_map_tab import TacticalMapTab
from product.ui.widgets.status_banner import DropStatus, DropReason
from product.ui.tabs import (
    analysis as analysis_tab_renderer,
    mission_overview as mission_overview_tab_renderer,
    sensor_telemetry,
    system_status,
)


def build_config_snapshot(threshold_pct: float) -> dict:
    """Build a valid CONFIG snapshot for schema compliance. AX-SNAPSHOT-CONTRACT-FIX-01."""
    return {
        "snapshot_type": "CONFIG",
        "threshold_pct": threshold_pct,
        "mission_mode": "TACTICAL",
        "n_samples": 1000,
        "telemetry": {},
        "decision": None,
        "hits": None,
        "P_hit": None,
        "ci_low": None,
        "ci_high": None,
        "impact_points": [],
        "cep50": None,
        "confidence_index": None,
        "wind_vector": None,
        "target_position": None,
        "target_radius": None,
        "random_seed": None,
        "decision_reason": None,
        "doctrine_description": None,
        "impact_velocity_stats": None,
        "robustness_status": None,
        "stability_index": None,
    }


class AppState(Enum):
    """Application state for Operator Mode workflow control."""
    NO_PAYLOAD = "no_payload"
    PAYLOAD_SELECTED = "payload_selected"
    EVALUATED = "evaluated"
    INVALIDATED = "invalidated"


class SimulationWorker(QThread):
    """One-shot simulation worker to keep UI responsive."""

    simulation_done = Signal(dict, str)
    simulation_failed = Signal(str, str)

    def __init__(self, config_override: dict, trigger: str, parent=None) -> None:
        super().__init__(parent)
        self.config_override = dict(config_override or {})
        self.trigger = trigger

    def run(self) -> None:
        try:
            t0 = time.perf_counter()
            snapshot = run_simulation_snapshot(
                config_override=self.config_override,
                include_advisory=True,
            )
            snapshot["compute_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            self.simulation_done.emit(snapshot, self.trigger)
        except Exception as exc:  # pragma: no cover - defensive path
            self.simulation_failed.emit(str(exc), self.trigger)


class MainWindow(QMainWindow):
    """Phase 1 desktop shell: structure + placeholders only."""

    def __init__(self) -> None:
        super().__init__()
        self.current_mode = "standard"
        self.system_mode = "SNAPSHOT"
        self.current_snapshot_id = None
        self.snapshot_active = False
        self.telemetry_worker = None
        self.evaluation_worker = None
        self.telemetry_state = TelemetryState()
        self.config_state = ConfigState()
        self._prev_wind_gradient: float | None = None
        self.simulation_running = False
        self._simulation_worker = None
        self._last_eval_time = None
        self._latest_snapshot = None
        self._snapshot_created_at = None
        self._last_snapshot_type: str | None = None
        self._last_telemetry = {}
        self._simulation_started_at = None
        self.auto_evaluate_paused = False
        self.mission_fig_op = None
        self.mission_canvas_op = None
        self.system_state = SystemState()
        # Application state control (Operator Mode only)
        self.app_state = AppState.NO_PAYLOAD
        self._last_applied_payload_key = None  # Track payload to detect changes
        self._mission_config_overrides: dict = {
            "mission_mode": "TACTICAL",
            "doctrine_mode": "BALANCED",
            "n_samples": 1000,
            "random_seed": 42,
        }
        self._telemetry_source = "mock"
        self._telemetry_file_path: str | None = None
        self._auto_eval_interval = "OFF"

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.auto_evaluate)

        # AX-EXECUTION-MODE-HYBRID-07: hybrid execution controls (Run Once / LIVE)
        self._execution_mode = "MANUAL"
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(200)  # 5 Hz
        self._live_timer.timeout.connect(self._auto_evaluate)

        self.setWindowTitle("SCYTHE")
        self.setMinimumSize(1200, 800)
        self._build_ui()
        self._apply_theme()
        self._refresh_mode_buttons()
        self._start_telemetry()
        self.tactical_map_controller = TacticalMapController(
            self.system_state, self.tactical_map, parent=self
        )
        self.tactical_map_controller.start()

        # Evaluation worker for continuous live mode
        self.evaluation_worker = EvaluationWorker(
            self.telemetry_state,
            self.config_state,
            parent=self,
        )
        self.evaluation_worker.result_ready.connect(self._handle_evaluation_result)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 0, 12, 12)
        root.setSpacing(12)

        self.system_mode = "SNAPSHOT"

        # Tab navigation and bottom status strip (full width).
        right_container = QWidget(central)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.main_tabs = QTabWidget(right_container)
        self.main_tabs.setObjectName("mainTabs")
        self.main_tabs.tabBar().setExpanding(False)
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        # Mission Overview: Standard mode uses special layout, Advanced uses canvas
        # Create tab pages with parent=None - Qt will reparent them when addTab() is called
        self.mission_tab_operator = self._build_mission_tab_operator(None)
        self.mission_tab_engineering, self.mission_fig, self.mission_canvas = self._build_canvas_tab(None)
        # Start with standard layout (default mode is standard)
        self.mission_tab = self.mission_tab_operator

        self.tactical_map = TacticalMapTab()
        self.tactical_map_widget = self.tactical_map.map_widget

        self.payload_tab = self._build_payload_tab(None)
        self.telemetry_tab, self.telemetry_fig, self.telemetry_canvas = self._build_canvas_tab(None)

        self.analysis_tab, self.analysis_fig, self.analysis_canvas = self._build_canvas_tab(None)

        self.system_tab, self.system_fig, self.system_canvas = self._build_canvas_tab(None)

        # Add tabs in schematic order: Tactical Map, Telemetry, Mission Config, Analysis, System Status
        self.main_tabs.addTab(self.tactical_map, "Tactical Map")
        self.main_tabs.addTab(self.telemetry_tab, "Telemetry")
        self.main_tabs.addTab(self.payload_tab, "Mission Configuration")
        self.main_tabs.setTabToolTip(self.main_tabs.indexOf(self.payload_tab), "Mission Configuration")
        self.main_tabs.addTab(self.analysis_tab, "Analysis")
        self.main_tabs.addTab(self.system_tab, "System Status")
        # Default tab: Tactical Map (index 0)
        self.main_tabs.setCurrentIndex(0)
        right_layout.addWidget(self.main_tabs, 1)

        self.status_strip = StatusStrip(right_container)
        right_layout.addWidget(self.status_strip)
        self.status_strip.setVisible(False)  # Footer (Snapshot ID, Telemetry) removed

        root.addWidget(right_container, 1)
        self.setCentralWidget(central)
        self._live_auto_eval_combo.currentTextChanged.connect(self._on_auto_eval_changed)
        self.target_radius_slider.valueChanged.connect(self._on_target_radius_slider_changed)
        self.target_radius_spinbox.valueChanged.connect(self._on_target_radius_spinbox_changed)
        self.status_strip.snapshot_label.setText("Snapshot ID: --- | Ready")
        self.status_strip.telemetry_label.setText("Telemetry: LIVE")
        # Initialize application state (Operator Mode only) - but don't restrict tabs
        if self.current_mode == "operator":
            self.app_state = AppState.NO_PAYLOAD
        # Seed config_state from defaults; init Tactical Map spinboxes and MissionConfigTab
        self._seed_config_state()
        with self.config_state.lock:
            cfg = dict(self.config_state.data)
        self.mission_config_tab.init_from_config(cfg)
        self.mission_config_tab.apply_system_mode(self.system_mode)
        self.tactical_map_widget._status_banner.navigate_to_tab.connect(self._switch_to_tab)
        self.tactical_map_widget._status_banner.set_status(
            DropStatus.NO_DROP, DropReason.MISSION_PARAMS_NOT_SET
        )
        tr = float(cfg.get("target_radius", 5.0))
        tr_clamp = min(50.0, max(0.5, tr))
        self.target_radius_spinbox.setValue(tr_clamp)
        self.target_radius_slider.setValue(int((tr_clamp - 0.5) / 0.5) + 1)
        self._push_config_to_worker()
        with self.config_state.lock:
            self._latest_snapshot = {
                "snapshot_type": "CONFIG",
                "threshold_pct": float(self.config_state.data.get("threshold_pct", 75.0)),
                "mission_mode": self.config_state.data.get("mission_mode", "TACTICAL"),
                "n_samples": int(self.config_state.data.get("n_samples", 1000)),
                "doctrine_mode": self.config_state.data.get("doctrine_mode", "BALANCED"),
                "timestamp": time.time(),
            }
        # Render all tabs normally
        self._render_mission_tab()
        self._render_analysis_tab()
        self._render_payload_tab()
        self._render_sensor_tab()
        self._render_system_tab()

    def _build_mission_tab_operator(self, parent: QWidget | None) -> QWidget:
        """Build Tactical Map tab: scrollable content, 3-card decision band, plot + advisory column."""
        tab = QWidget(parent)
        tab.setStyleSheet("background-color: #0d140d;")
        tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout = QVBoxLayout(tab)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll_area = QScrollArea(tab)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll_area.setStyleSheet("QScrollArea { background-color: #0d140d; border: none; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: #0d140d;")
        content_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(5, 5, 5, 5)
        content_layout.setSpacing(4)

        # ----- STEP 3: TOP DECISION BAND (3 cards horizontal) -----
        decision_row = QHBoxLayout()
        decision_row.setSpacing(5)
        decision_row.addStretch(1)

        # AX-EXECUTION-MODE-HYBRID-07: Execution controls (Run Once / LIVE)
        execution_group = QFrame(content_widget)
        execution_group.setObjectName("executionGroup")
        execution_group.setStyleSheet(
            "QFrame#executionGroup { border: 1px solid #1a2a1a; border-radius: 6px; background-color: #0d140d; }"
        )
        execution_layout = QVBoxLayout(execution_group)
        execution_layout.setContentsMargins(8, 2, 8, 4)
        execution_layout.setSpacing(4)
        exec_label = QLabel("Execution", execution_group)
        exec_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        exec_label.setStyleSheet("font-size: 16px; color: #22cc22;")
        execution_layout.addWidget(exec_label)
        exec_btn_row = QHBoxLayout()
        exec_btn_row.setSpacing(4)
        exec_btn_style = "font-size: 14px; color: #22cc22;"
        self.run_once_btn = QPushButton("Run Once", execution_group)
        self.run_once_btn.setStyleSheet(exec_btn_style)
        self.run_once_btn.clicked.connect(self._on_run_once_clicked)
        self.live_btn = QPushButton("LIVE", execution_group)
        self.live_btn.setStyleSheet(exec_btn_style)
        self.live_btn.clicked.connect(self._on_live_clicked)
        exec_btn_row.addWidget(self.run_once_btn, 1)
        exec_btn_row.addWidget(self.live_btn, 1)
        execution_layout.addLayout(exec_btn_row)
        self.live_mode_label = QLabel("", execution_group)
        self.live_mode_label.setStyleSheet("font-size: 10px; color: #ff4444; font-weight: bold;")
        execution_layout.addWidget(self.live_mode_label)
        self.live_mode_label.hide()
        self._live_options_row = QHBoxLayout()
        self._live_telemetry_combo = QComboBox(execution_group)
        self._live_telemetry_combo.addItem("Mock", "mock")
        self._live_telemetry_combo.addItem("File", "file")
        self._live_telemetry_combo.setStyleSheet("font-size: 11px; min-height: 24px;")
        self._live_telemetry_path = QLineEdit(execution_group)
        self._live_telemetry_path.setPlaceholderText("Path to CSV...")
        self._live_telemetry_path.setStyleSheet("font-size: 11px; min-height: 24px;")
        self._live_telemetry_apply_btn = QPushButton("Apply", execution_group)
        self._live_telemetry_apply_btn.setStyleSheet("font-size: 11px;")
        self._live_telemetry_apply_btn.clicked.connect(self._on_telemetry_source_apply)
        self._live_auto_eval_combo = QComboBox(execution_group)
        self._live_auto_eval_combo.addItems(["OFF", "1s", "2s"])
        self._live_auto_eval_combo.setStyleSheet("font-size: 11px; min-height: 24px;")
        _live_lbl = QLabel("Telemetry:", execution_group)
        _live_lbl.setStyleSheet("font-size: 11px; color: #86a886;")
        self._live_options_row.addWidget(_live_lbl)
        self._live_options_row.addWidget(self._live_telemetry_combo)
        self._live_options_row.addWidget(self._live_telemetry_path, 1)
        self._live_options_row.addWidget(self._live_telemetry_apply_btn)
        _ae_lbl = QLabel("Auto-eval:", execution_group)
        _ae_lbl.setStyleSheet("font-size: 11px; color: #86a886;")
        self._live_options_row.addWidget(_ae_lbl)
        self._live_options_row.addWidget(self._live_auto_eval_combo)
        execution_layout.addLayout(self._live_options_row)
        decision_row.addWidget(execution_group)

        # 3.1 LEFT CARD — Mission Inputs (Mode, HIT %, HITS)
        card_inputs = QFrame(content_widget)
        card_inputs.setObjectName("decisionCardInputs")
        card_inputs.setStyleSheet("QFrame#decisionCardInputs { border: 1px solid #1a2a1a; border-radius: 6px; background-color: #0d140d; }")
        card_inputs_layout = QVBoxLayout(card_inputs)
        card_inputs_layout.setContentsMargins(8, 6, 8, 6)
        card_inputs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.mode_value_label = QLabel("Mode:", card_inputs)
        self.mode_value_label.setObjectName("decisionFieldHighlight")
        self.mode_value_label.setTextFormat(Qt.TextFormat.RichText)
        self.p_hit_value_label = QLabel("HIT %:", card_inputs)
        self.p_hit_value_label.setObjectName("decisionFieldHighlight")
        self.p_hit_value_label.setTextFormat(Qt.TextFormat.RichText)
        self.hits_value_label = QLabel("HITS:", card_inputs)
        self.hits_value_label.setObjectName("decisionFieldHighlight")
        self.hits_value_label.setTextFormat(Qt.TextFormat.RichText)
        self.stability_grade_label = QLabel("Stability:", card_inputs)
        self.stability_grade_label.setObjectName("decisionFieldHighlight")
        self.stability_grade_label.setTextFormat(Qt.TextFormat.RichText)
        card_inputs_layout.addWidget(self.mode_value_label)
        card_inputs_layout.addWidget(self.p_hit_value_label)
        card_inputs_layout.addWidget(self.hits_value_label)
        card_inputs_layout.addWidget(self.stability_grade_label)
        decision_row.addWidget(card_inputs, 1)

        # 3.2 CENTER CARD — Decision State (larger area; READY/PAUSED/DROP/NO DROP)
        self.decision_state_card = QFrame(content_widget)
        self.decision_state_card.setObjectName("decisionStateCard")
        self.decision_state_card.setStyleSheet("QFrame#decisionStateCard { border: 2px solid #1a2a1a; border-radius: 6px; background-color: #0d140d; }")
        card_state_layout = QVBoxLayout(self.decision_state_card)
        card_state_layout.setContentsMargins(8, 6, 8, 6)
        card_state_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.decision_label = QLabel("READY", self.decision_state_card)
        self.decision_label.setObjectName("decisionLabel")
        self.margin_label = QLabel("Margin: --", self.decision_state_card)
        self.margin_label.setObjectName("marginLabel")
        self.paused_message_label = QLabel("", self.decision_state_card)
        self.paused_message_label.setObjectName("pausedMessage")
        self.paused_message_label.setWordWrap(True)
        self.paused_message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.paused_message_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paused_message_label.installEventFilter(self)
        self.paused_message_label.hide()
        self._paused_target_tab = None
        self._current_decision = ""
        self.decision_state_card.installEventFilter(self)
        self.decision_label.installEventFilter(self)
        card_state_layout.addWidget(self.decision_label)
        card_state_layout.addWidget(self.margin_label)
        card_state_layout.addWidget(self.paused_message_label)
        decision_row.addWidget(self.decision_state_card, 3)

        # Hidden compute button (preserved for data bindings)
        self.evaluate_container = QWidget(content_widget)
        self.evaluate_container.hide()
        self.evaluate_btn = QPushButton("Compute", self.evaluate_container)
        self.evaluate_btn.clicked.connect(self._on_evaluate_clicked)

        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(80)
        self._glow_timer.timeout.connect(lambda: None)

        # 3.3 RIGHT CARD — Statistical Summary (95% CI, CI width, Sample count, CEP50)
        card_stats = QFrame(content_widget)
        card_stats.setObjectName("decisionCardStats")
        card_stats.setStyleSheet("QFrame#decisionCardStats { border: 1px solid #1a2a1a; border-radius: 6px; background-color: #0d140d; }")
        card_stats_layout = QVBoxLayout(card_stats)
        card_stats_layout.setContentsMargins(8, 6, 8, 6)
        card_stats_layout.setSpacing(5)
        self.ci_95_label = QLabel("95% CI:", card_stats)
        self.ci_95_label.setObjectName("decisionFieldHighlight")
        self.ci_95_label.setTextFormat(Qt.TextFormat.RichText)
        self.ci_width_label = QLabel("CI width:", card_stats)
        self.ci_width_label.setObjectName("decisionFieldHighlight")
        self.ci_width_label.setTextFormat(Qt.TextFormat.RichText)
        self.sample_count_label = QLabel("Sample count:", card_stats)
        self.sample_count_label.setObjectName("decisionFieldHighlight")
        self.sample_count_label.setTextFormat(Qt.TextFormat.RichText)
        self.cep50_value_label = QLabel("CEP50:", card_stats)
        self.cep50_value_label.setObjectName("decisionFieldHighlight")
        self.cep50_value_label.setTextFormat(Qt.TextFormat.RichText)
        card_stats_layout.addWidget(self.ci_95_label)
        card_stats_layout.addWidget(self.ci_width_label)
        card_stats_layout.addWidget(self.sample_count_label)
        card_stats_layout.addWidget(self.cep50_value_label)
        decision_row.addWidget(card_stats, 1)

        # 3.4 NEW SIMULATION CARD — trigger fresh simulation with new config
        self.new_sim_card = QFrame(content_widget)
        self.new_sim_card.setObjectName("newSimCard")
        self.new_sim_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_sim_card.installEventFilter(self)
        new_sim_layout = QVBoxLayout(self.new_sim_card)
        new_sim_layout.setContentsMargins(8, 6, 8, 6)
        new_sim_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._new_sim_icon = QLabel("\u27f3", self.new_sim_card)
        self._new_sim_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._new_sim_icon.setStyleSheet("font-size: 28px; color: #2cff05; border: none; background: transparent;")
        self._new_sim_title = QLabel("New Simulation", self.new_sim_card)
        self._new_sim_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._new_sim_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2cff05; border: none; background: transparent;")
        self._new_sim_subtitle = QLabel("Reconfigure & Run", self.new_sim_card)
        self._new_sim_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._new_sim_subtitle.setStyleSheet("font-size: 12px; color: #2cff05; border: none; background: transparent;")
        new_sim_layout.addWidget(self._new_sim_icon)
        new_sim_layout.addWidget(self._new_sim_title)
        new_sim_layout.addWidget(self._new_sim_subtitle)
        self._apply_new_sim_card_style(hovered=False)
        self._new_sim_icon.installEventFilter(self)
        self._new_sim_title.installEventFilter(self)
        self._new_sim_subtitle.installEventFilter(self)
        decision_row.addWidget(self.new_sim_card, 1)
        decision_row.addStretch(1)

        content_layout.addLayout(decision_row)

        # ----- SLIDER ROW — each row: [label | slider] + [spinbox]. Sliders end at Statistical Summary
        # boundary; red box (New Simulation width) holds spinboxes. No overlap.
        slider_row = QWidget(content_widget)
        slider_row.setStyleSheet(
            "background-color: #0d140d; border: 1px solid #1a2a1a; border-radius: 6px; padding: 5px;"
        )
        slider_row.setFixedHeight(34)
        slider_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        slider_main = QVBoxLayout(slider_row)
        slider_main.setContentsMargins(5, 1, 5, 1)
        slider_main.setSpacing(1)

        _slider_style = """
            QSlider { border: none; background: transparent; }
            QSlider::groove:horizontal { border: none; height: 7px; background: #1a2a1a; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #2cff05; border-radius: 3px; }
            QSlider::handle:horizontal { width: 13px; margin: -3px 0; background: #2cff05; border-radius: 6px; }
        """
        _slider_label_style = "color: #2cff05; font-size: 15px; border: none; background: transparent;"
        _spinbox_style = "color: #2cff05; border: none; background: transparent; padding: 0 2px; margin-top: -6px; font-size: 15px;"

        # Row: Target radius (threshold moved to Mission Config tab)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        left2 = QHBoxLayout()
        left2.setSpacing(8)
        tr_label = QLabel("Target radius (m):", slider_row)
        tr_label.setStyleSheet(_slider_label_style)
        left2.addWidget(tr_label)
        self.target_radius_slider = NoWheelSlider(Qt.Orientation.Horizontal, slider_row)
        self.target_radius_slider.setRange(1, 100)
        self.target_radius_slider.setFixedHeight(23)
        self.target_radius_slider.setStyleSheet(_slider_style)
        left2.addWidget(self.target_radius_slider, 1)
        row2.addLayout(left2, 5)
        self.target_radius_spinbox = NoWheelDoubleSpinBox(slider_row)
        self.target_radius_spinbox.setRange(0.5, 50.0)
        self.target_radius_spinbox.setSingleStep(0.5)
        self.target_radius_spinbox.setDecimals(1)
        self.target_radius_spinbox.setFixedWidth(96)
        self.target_radius_spinbox.setFixedHeight(22)
        self.target_radius_spinbox.setFrame(False)
        self.target_radius_spinbox.setStyleSheet(_spinbox_style)
        row2.addWidget(self.target_radius_spinbox, 1, Qt.AlignmentFlag.AlignVCenter)

        slider_main.addLayout(row2)
        content_layout.addWidget(slider_row)

        # ----- STEP 4: MAIN BODY — Plot (left) + Advisory column (right, original position) -----
        main_body_row = QHBoxLayout()
        main_body_row.setSpacing(5)

        # 4.1 Plot container with mode toggles overlaid top-right
        plot_container = QWidget(content_widget)
        plot_container.setStyleSheet("background-color: #0d140d;")
        plot_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        plot_grid = QGridLayout(plot_container)
        plot_grid.setContentsMargins(0, 0, 0, 0)
        plot_grid.setSpacing(0)

        self.mission_fig_op = qt_bridge.create_figure(figsize=(6.0, 2.9))
        self.mission_canvas_op = qt_bridge.create_canvas(self.mission_fig_op)
        self.mission_canvas_op.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mission_canvas_op.setMinimumHeight(230)
        self.mission_canvas_op.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mission_canvas_op.installEventFilter(self)
        plot_grid.addWidget(self.mission_canvas_op, 0, 0)

        mode_toggle_container = QWidget(plot_container)
        mode_toggle_container.setStyleSheet("background-color: #0d140d; border: 1px solid #1a2a1a; border-radius: 4px;")
        mode_toggle_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        mode_toggle_layout = QHBoxLayout(mode_toggle_container)
        mode_toggle_layout.setContentsMargins(0, 4, 4, 0)
        mode_toggle_layout.setSpacing(2)
        mode_toggle_layout.addStretch(1)
        btn_style = "font-size: 11px; min-height: 27px; padding: 3px 8px;"
        self.operator_btn = QPushButton("Standard View", mode_toggle_container)
        self.operator_btn.setCheckable(True)
        self.operator_btn.setStyleSheet(btn_style)
        self.operator_btn.clicked.connect(lambda: self._set_mode("standard"))
        self.engineering_btn = QPushButton("Advanced View", mode_toggle_container)
        self.engineering_btn.setCheckable(True)
        self.engineering_btn.setStyleSheet(btn_style)
        self.engineering_btn.clicked.connect(lambda: self._set_mode("advanced"))
        mode_toggle_layout.addWidget(self.operator_btn)
        mode_toggle_layout.addWidget(self.engineering_btn)
        plot_grid.addWidget(mode_toggle_container, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        self.mission_nav_toolbar = NavigationToolbar2QT(self.mission_canvas_op, plot_container)
        self.mission_nav_toolbar.hide()
        plot_grid.addWidget(self.mission_nav_toolbar, 1, 0)

        main_body_row.addWidget(plot_container, 3)

        # 4.2 Advisory column — right of plot (original position, below New Simulation)
        advisory_column = QFrame(content_widget)
        advisory_column.setObjectName("advisoryColumn")
        advisory_column.setStyleSheet("QFrame#advisoryColumn { border: 1px solid #1a2a1a; border-radius: 6px; background-color: #0a110a; }")
        advisory_col_layout = QVBoxLayout(advisory_column)
        advisory_col_layout.setContentsMargins(8, 6, 8, 6)
        advisory_col_layout.setSpacing(4)

        self.advisory_section_title = QLabel("Advisory", advisory_column)
        self.advisory_section_title.setObjectName("groupTitle")
        self.advisory_section_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advisory_section_title.setStyleSheet("color: #2cff05; font-weight: bold; font-size: 16px;")
        self.advisory_reason_label = QLabel("Reason: —", advisory_column)
        self.advisory_reason_label.setObjectName("advisoryFieldHighlight")
        self.advisory_reason_label.setWordWrap(True)
        self.advisory_stat_note_label = QLabel("Statistical note: —", advisory_column)
        self.advisory_stat_note_label.setObjectName("advisoryFieldHighlight")
        self.advisory_stat_note_label.setWordWrap(True)
        self.advisory_actions_label = QLabel("Actions: —", advisory_column)
        self.advisory_actions_label.setObjectName("advisoryFieldHighlight")
        self.advisory_actions_label.setWordWrap(True)
        advisory_col_layout.addWidget(self.advisory_section_title)
        advisory_col_layout.addWidget(self.advisory_reason_label)
        advisory_col_layout.addWidget(self.advisory_stat_note_label)
        advisory_col_layout.addWidget(self.advisory_actions_label)

        separator = QFrame(advisory_column)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #1a2a1a; max-height: 1px;")
        advisory_col_layout.addWidget(separator)

        self.current_factors_title = QLabel("Current Factors", advisory_column)
        self.current_factors_title.setObjectName("groupTitle")
        self.current_factors_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_factors_title.setStyleSheet("color: #2cff05; font-weight: bold; font-size: 16px;")
        self.wind_label = QLabel("Wind: <span style='color:#e8e8e8'>--</span>", advisory_column)
        self.wind_label.setObjectName("advisoryFieldHighlight")
        self.wind_label.setTextFormat(Qt.TextFormat.RichText)
        self.altitude_label = QLabel("Altitude: <span style='color:#e8e8e8'>--</span>", advisory_column)
        self.altitude_label.setObjectName("advisoryFieldHighlight")
        self.altitude_label.setTextFormat(Qt.TextFormat.RichText)
        self.speed_label = QLabel("Speed: <span style='color:#e8e8e8'>--</span>", advisory_column)
        self.speed_label.setObjectName("advisoryFieldHighlight")
        self.speed_label.setTextFormat(Qt.TextFormat.RichText)
        advisory_col_layout.addWidget(self.current_factors_title)
        advisory_col_layout.addWidget(self.wind_label)
        advisory_col_layout.addWidget(self.altitude_label)
        advisory_col_layout.addWidget(self.speed_label)
        self.wind_sensitivity_label = QLabel("Wind Sensitivity: —", advisory_column)
        self.wind_sensitivity_label.setObjectName("advisoryFieldHighlight")
        self.wind_sensitivity_label.setTextFormat(Qt.TextFormat.RichText)
        advisory_col_layout.addWidget(self.wind_sensitivity_label)
        self.drift_label = QLabel("Drift: —", advisory_column)
        self.drift_label.setObjectName("advisoryFieldHighlight")
        self.drift_label.setTextFormat(Qt.TextFormat.RichText)
        advisory_col_layout.addWidget(self.drift_label)
        self.release_corridor_label = QLabel("Release Corridor: —", advisory_column)
        self.release_corridor_label.setObjectName("advisoryFieldHighlight")
        self.release_corridor_label.setTextFormat(Qt.TextFormat.RichText)
        advisory_col_layout.addWidget(self.release_corridor_label)
        advisory_col_layout.addStretch(1)

        main_body_row.addWidget(advisory_column, 1)
        content_layout.addLayout(main_body_row)

        # Invalidation message (hidden by default; reused for INVALIDATED state)
        self.invalidation_label = QLabel("Configuration changed. Re-evaluation required.", content_widget)
        self.invalidation_label.setObjectName("invalidationLabel")
        self.invalidation_label.setStyleSheet("color: #ffaa00; font-weight: bold;")
        self.invalidation_label.hide()
        content_layout.addWidget(self.invalidation_label)

        scroll_area.setWidget(content_widget)
        root_layout.addWidget(scroll_area)
        return tab

    def _build_canvas_tab(self, parent: QWidget | None) -> tuple[QWidget, object, object]:
        tab = QWidget(parent)
        tab.setStyleSheet("background-color: #0d140d;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        fig = qt_bridge.create_figure(figsize=(9.5, 5.8))
        canvas = qt_bridge.create_canvas(fig)
        layout.addWidget(canvas, 1)
        # Add navigation toolbar (hidden but functional for zoom/pan)
        nav_toolbar = NavigationToolbar2QT(canvas, tab)
        nav_toolbar.hide()  # Hide toolbar but keep functionality
        layout.addWidget(nav_toolbar)
        return tab, fig, canvas

    def _build_payload_tab(self, parent: QWidget | None) -> QWidget:
        """Mission Config tab: Mission Mode, accordion, Commit."""
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: #0d140d;")
        self.mission_config_tab = MissionConfigTab(scroll)
        scroll.setWidget(self.mission_config_tab)
        self.mission_config_tab.config_committed.connect(self._on_mission_config_committed)
        self.mission_config_tab.dirty_changed.connect(self._on_mission_config_dirty_changed)
        self.mission_config_tab.threshold_changed.connect(self._on_mission_config_threshold_changed)
        return scroll

    def _is_mission_ready(self) -> bool:
        """AX-MISSION-READINESS-GATE-08: True iff payload configured."""
        with self.config_state.lock:
            pid = self.config_state.data.get("payload_id")
        return pid is not None and str(pid).strip() != ""

    def _is_payload_selected_in_config(self, cfg: dict) -> bool:
        """True iff cfg contains a non-empty payload_id (for commit validation)."""
        pid = cfg.get("payload_id")
        return pid is not None and str(pid).strip() != ""

    def _show_warning(self, message: str) -> None:
        """Show mission readiness warning dialog."""
        QMessageBox.warning(self, "Mission Not Ready", message)

    @Slot(float)
    def _on_mission_config_threshold_changed(self, value: float) -> None:
        """Update config_state when threshold changes in Mission Config tab."""
        with self.config_state.lock:
            self.config_state.data["threshold_pct"] = value
        self._render_mission_tab()

    @Slot(dict)
    def _on_mission_config_committed(self, cfg: dict) -> None:
        """Push Mission Config tab values to config_state."""
        if not self._is_payload_selected_in_config(cfg):
            self._show_warning("Select and configure payload before committing mission.")
            return
        committed_cfg = dict(cfg)
        # Ensure simulation_fidelity is always present on commit; default to advanced.
        committed_cfg.setdefault("simulation_fidelity", "advanced")
        self._mission_config_overrides = committed_cfg
        self._push_config_to_worker()
        with self.config_state.lock:
            th = float(self.config_state.data.get("threshold_pct", 75.0))
        base = build_config_snapshot(th)
        base.update({
            "mission_mode": cfg.get("mission_mode", "TACTICAL"),
            "doctrine_mode": cfg.get("doctrine_mode", "BALANCED"),
            "n_samples": cfg.get("n_samples", 1000),
            "timestamp": time.time(),
        })
        self._latest_snapshot = base
        self.app_state = AppState.PAYLOAD_SELECTED
        with self.system_state.lock:
            self.system_state.mission_committed = True
        self._update_summary_strip_after_commit()
        self.main_tabs.setCurrentIndex(0)
        self._render_mission_tab()
        self._render_system_tab()
        self._update_app_state_ui()

    @Slot(bool)
    def _on_mission_config_dirty_changed(self, dirty: bool) -> None:
        """Update Commit button glow (handled inside MissionConfigTab)."""
        pass

    def _update_summary_strip_after_commit(self) -> None:
        """Update status strip from snapshot only. Raises RuntimeError if snapshot missing."""
        if self._latest_snapshot is None:
            raise RuntimeError("Summary strip requires snapshot; _latest_snapshot is None.")
        mode = str(self._latest_snapshot.get("mission_mode", "TACTICAL"))
        n = int(self._latest_snapshot.get("n_samples", 1000))
        self.status_strip.snapshot_label.setText(
            f"Config committed | {mode} | n={n}"
        )

    def _refresh_payload_name_combo(self) -> None:
        pass

    def _on_payload_category_changed(self, _text: str) -> None:
        pass

    def _on_payload_apply_clicked(self) -> None:
        pass

    def _get_paused_reason(self) -> dict | None:
        """Return paused info dict {reason, target_tab} or None if ready to run."""
        if self.app_state == AppState.NO_PAYLOAD:
            return {"reason": "Configure Payload", "target_tab": self.payload_tab}
        if self.app_state == AppState.INVALIDATED:
            return {"reason": "Re-apply Payload", "target_tab": self.payload_tab}
        if self.simulation_running:
            return {"reason": "Computing — Standby", "target_tab": None}
        if self.system_mode == "LIVE" and self.telemetry_worker is None:
            return {"reason": "Calibrate Telemetry", "target_tab": self.system_tab}
        return None

    def _render_mission_tab(self) -> None:
        snapshot = self._latest_snapshot or {}
        snapshot_type = snapshot.get("snapshot_type")
        if snapshot_type not in ("CONFIG", "EVALUATION", "ERROR"):
            # Before init completes, snapshot may be empty; treat as CONFIG
            snapshot_type = "CONFIG"
            snapshot = dict(snapshot)
            snapshot["snapshot_type"] = "CONFIG"
            snapshot.setdefault("threshold_pct", 75.0)

        if snapshot_type == "ERROR":
            self._prev_wind_gradient = None
            self._push_config_to_worker()
            try:
                validate_snapshot(snapshot)
            except ValueError:
                pass
            err_msg = snapshot.get("error_message", "Unknown error")
            self._render_mission_tab_operator_error(err_msg)
            self._render_analysis_tab()
            return

        try:
            validate_snapshot(snapshot)
        except ValueError as e:
            self._latest_snapshot = {"snapshot_type": "ERROR", "error_message": str(e)}
            self._render_mission_tab_operator_error(str(e))
            self._render_analysis_tab()
            return

        if snapshot_type == "CONFIG":
            self._prev_wind_gradient = None
            self._push_config_to_worker()
            # CONFIG: READY / PAUSED from operational blockers only
            paused_info = self._get_paused_reason()
            decision = "PAUSED" if paused_info else "READY"
            threshold = float(snapshot.get("threshold_pct", 75.0))
            self._log_state_transition(snapshot_type)
            self._render_mission_tab_operator(snapshot, decision, 0.0, 0.0, threshold, None, [], paused_info, config_only=True)
            return

        # EVALUATION snapshot — snapshot is sole authority (AX-DECISION-BLOCK-STATE-ALIGNMENT-01)
        impact_points = snapshot.get("impact_points", [])
        p_hit = float(snapshot.get("P_hit", 0.0) or 0.0)
        cep50 = float(snapshot.get("cep50", 0.0) or 0.0)
        advisory = snapshot.get("advisory")
        threshold = float(snapshot.get("threshold_pct", 75.0))
        decision = str(snapshot.get("decision", "")).strip().upper()
        if decision not in ("DROP", "NO DROP"):
            decision = "DROP" if (p_hit * 100.0) >= threshold else "NO DROP"
        if advisory is not None:
            raw = str(getattr(advisory, "current_feasibility", "") or "").strip().upper()
            if raw in ("DROP", "NO DROP", "NO_DROP"):
                decision = raw.replace("_", " ")
        robustness = snapshot.get("robustness_status") or ""
        paused_info = None
        self._log_state_transition(snapshot_type)
        self._render_mission_tab_operator(
            snapshot, decision, p_hit, cep50, threshold, advisory, impact_points, paused_info,
            config_only=False, robustness_status=robustness,
        )

    def _log_state_transition(self, new_type: str) -> None:
        """AX-OBSERVABILITY-03: Log snapshot type transitions."""
        old = self._last_snapshot_type
        if old != new_type:
            print("STATE TRANSITION:", old, "→", new_type)
            self._last_snapshot_type = new_type

    def _render_mission_tab_operator_error(self, error_message: str) -> None:
        """Render ERROR state. AX-ERROR-STATE-HARDEN-03: fully sanitize UI, no stale data."""
        self.decision_label.setText("SYSTEM ERROR")
        self.decision_label.setStyleSheet("color: #c83030; font-size: 28px; font-weight: bold; padding: 8px;")
        self.decision_state_card.setStyleSheet(
            "QFrame#decisionStateCard { border: 2px solid #c83030; border-radius: 6px; background-color: #0d140d; }"
        )
        self.paused_message_label.setText(error_message[:80] + ("..." if len(error_message) > 80 else ""))
        self.paused_message_label.show()
        self.margin_label.hide()
        self._current_decision = "ERROR"
        # Hide stats panel, clear impact cloud, clear advisory (AX-ERROR-STATE-HARDEN-03)
        self.p_hit_value_label.setText("HIT %:")
        self.hits_value_label.setText("HITS:")
        self.stability_grade_label.setText("Stability:")
        self.sample_count_label.setText("Sample count:")
        self.cep50_value_label.setText("CEP50:")
        self.ci_95_label.setText("95% CI:")
        self.ci_width_label.setText("CI width:")
        self.advisory_reason_label.setText("Reason: —")
        self.advisory_stat_note_label.setText("Statistical note: —")
        self.advisory_actions_label.setText("Actions: —")
        self.wind_label.setText("Wind: <span style='color:#e8e8e8'>—</span>")
        self.altitude_label.setText("Altitude: <span style='color:#e8e8e8'>—</span>")
        self.speed_label.setText("Speed: <span style='color:#e8e8e8'>—</span>")
        if hasattr(self, "wind_sensitivity_label"):
            self.wind_sensitivity_label.setText("Wind Sensitivity: —")
        if hasattr(self, "drift_label"):
            self.drift_label.setText("Drift: —")
        if hasattr(self, "release_corridor_label"):
            self.release_corridor_label.setText("Release Corridor: —")
        self.mission_fig_op.clear()
        self.mission_fig_op.add_subplot(1, 1, 1).set_axis_off()
        if hasattr(self, "mission_canvas_op") and self.mission_canvas_op is not None:
            self.mission_canvas_op.draw_idle()

    def _render_mission_tab_operator(
        self, snapshot, decision, p_hit, cep50, threshold, advisory, impact_points,
        paused_info=None, config_only: bool = False, robustness_status: str = "",
    ) -> None:
        """Render Tactical Map tab. All data from snapshot only."""
        snapshot_type = snapshot.get("snapshot_type")
        if snapshot_type not in ("CONFIG", "EVALUATION"):
            snapshot_type = "CONFIG"
            snapshot = dict(snapshot)
            snapshot["snapshot_type"] = "CONFIG"

        telem = snapshot.get("telemetry") or {}
        n_samples = int(snapshot.get("n_samples", 1000)) if snapshot.get("n_samples") is not None else 1000
        margin_pct = (p_hit * 100.0) - threshold if not config_only else 0.0
        decision_upper = (decision or "").strip().upper()

        # --- Color map (decision mapping unchanged) ---
        # Only green tones (DROP, READY) adjusted by mission_mode. Red and yellow unchanged.
        mission_mode = str(snapshot.get("mission_mode", "TACTICAL")).strip().upper()
        if mission_mode not in ("TACTICAL", "HUMANITARIAN"):
            mission_mode = "TACTICAL"
        base_map = {
            "DROP":    {"text": "#2cff05", "border": "#2cff05"},
            "NO DROP": {"text": "#c83030", "border": "#c83030"},
            "READY":   {"text": "#6aaf6a", "border": "#3a5a3a"},
            "PAUSED":  {"text": "#d4a017", "border": "#d4a017"},
        }
        raw = base_map.get(decision_upper, base_map["READY"])
        if decision_upper in ("DROP", "READY"):
            colors = {
                "text": adjust_color_intensity(raw["text"], mission_mode),
                "border": adjust_color_intensity(raw["border"], mission_mode),
            }
        else:
            colors = {"text": raw["text"], "border": raw["border"]}
        # AX-FRAGILITY-SURFACE-20: Override border by fragility zone when available
        fragility = snapshot.get("fragility_state") or {}
        zone = fragility.get("zone", "")
        if zone == "STABLE-ZONE":
            colors["border"] = "#2cff05"
        elif zone == "EDGE-ZONE":
            colors["border"] = "#c83030"
        elif zone == "TRANSITION-ZONE":
            colors["border"] = "#d4a017"
        self._current_decision = decision_upper
        # AX-MISSION-READINESS-GATE-08: Show CONFIGURE PAYLOAD when that's the blocker
        display_text = decision_upper
        if decision_upper == "PAUSED" and paused_info and paused_info.get("reason") == "Configure Payload":
            display_text = "CONFIGURE PAYLOAD"
        elif decision_upper in ("DROP", "NO DROP") and robustness_status:
            display_text = f"{decision_upper} ({robustness_status})"
        # READY: 4px border (~2px zoom), static; others: 2px border. No glow animation.
        border_px = 4 if decision_upper == "READY" else 2
        self.decision_state_card.setStyleSheet(
            f"QFrame#decisionStateCard {{ border: {border_px}px solid {colors['border']}; border-radius: 6px; background-color: #0d140d; }}"
        )
        if decision_upper == "READY":
            self.decision_state_card.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.decision_state_card.setCursor(Qt.CursorShape.ArrowCursor)
        self.decision_label.setText(display_text)
        self.decision_label.setStyleSheet(f"color: {colors['text']}; font-size: 32px; font-weight: bold; padding: 8px;")

        # PAUSED / READY caption
        if decision_upper == "PAUSED" and paused_info:
            self.margin_label.hide()
            reason = paused_info.get("reason", "System Paused")
            self._paused_target_tab = paused_info.get("target_tab")
            self.paused_message_label.setText(reason)
            self.paused_message_label.setStyleSheet(
                f"color: {colors['text']}; font-size: 11px; padding: 4px;"
            )
            self.paused_message_label.setToolTip(reason)
            self.paused_message_label.show()
        elif decision_upper == "READY":
            self.margin_label.hide()
            self._paused_target_tab = None
            self.paused_message_label.setText("Click to Start Simulation")
            self.paused_message_label.setToolTip("Click to start simulation")
            self.paused_message_label.setStyleSheet(
                f"color: {colors['text']}; font-size: 11px; padding: 4px;"
            )
            self.paused_message_label.show()
        else:
            self.paused_message_label.hide()
            self._paused_target_tab = None
            self.margin_label.show()

        # Left card: Mode, HIT %, HITS, Stability (heading green, value off-white)
        _v = "color:#e8e8e8"
        mode_display = "Standard" if self.current_mode == "standard" else "Advanced"
        self.mode_value_label.setText(f"Mode: <span style='{_v}'>{mode_display}</span>")
        if config_only:
            self.p_hit_value_label.setText("HIT %:")
            self.hits_value_label.setText("HITS:")
            self.margin_label.hide()
            self.stability_grade_label.setText("Stability:")
            self.sample_count_label.setText("Sample count:")
            self.cep50_value_label.setText("CEP50:")
            self.ci_95_label.setText("95% CI:")
            self.ci_width_label.setText("CI width:")
        elif snapshot_type == "EVALUATION":
            print("FIELD NAME HIT % SOURCE:", "p_hit (snapshot.P_hit)")
            print("FIELD VALUE HIT %:", p_hit * 100.0)
            self.p_hit_value_label.setText(f"HIT %: <span style='{_v}'>{p_hit * 100.0:.1f}%</span>")
            hits_val = snapshot.get("hits")
            n_val = int(snapshot.get("n_samples", 0) or n_samples or 0)
            if hits_val is not None and n_val > 0:
                hits_display = int(hits_val)
                print("FIELD NAME HITS SOURCE:", "snapshot.hits, snapshot.n_samples")
                print("FIELD VALUE HITS:", hits_display, "/", n_val)
                self.hits_value_label.setText(f"HITS: <span style='{_v}'>{hits_display}/{n_val}</span>")
            else:
                self.hits_value_label.setText(f"HITS: <span style='{_v}'>—/—</span>")
            self.margin_label.setText(f"Margin: {margin_pct:+.1f}%")
            if margin_pct >= 0:
                margin_color = adjust_color_intensity("#2cff05", mission_mode)
            else:
                margin_color = "#c83030"  # Red unchanged
            self.margin_label.setStyleSheet(f"color: {margin_color};")
            ci_val = float(snapshot.get("confidence_index") or 0.5)
            stab = "High" if ci_val >= 0.75 else ("Moderate" if ci_val >= 0.50 else "Low")
            print("FIELD NAME Stability SOURCE:", "snapshot.confidence_index")
            print("FIELD VALUE Stability:", stab)
            self.stability_grade_label.setText(f"Stability: <span style='{_v}'>{stab}</span>")
            print("FIELD NAME Sample count SOURCE:", "snapshot.n_samples")
            print("FIELD VALUE Sample count:", n_samples)
            self.sample_count_label.setText(f"Sample count: <span style='{_v}'>{n_samples}</span>")
            print("FIELD NAME CEP50 SOURCE:", "snapshot.cep50")
            print("FIELD VALUE CEP50:", cep50)
            self.cep50_value_label.setText(f"CEP50: <span style='{_v}'>{cep50:.2f} m</span>")
            # Wilson CI from snapshot only (no normal approximation)
            ci_lo = snapshot.get("ci_low")
            ci_hi = snapshot.get("ci_high")
            if ci_lo is not None and ci_hi is not None:
                ci_w = (ci_hi - ci_lo) * 100.0
                print("FIELD NAME 95% CI SOURCE:", "snapshot.ci_low, snapshot.ci_high")
                print("FIELD VALUE 95% CI:", ci_lo, "-", ci_hi)
                print("FIELD NAME CI width SOURCE:", "(ci_high - ci_low) * 100")
                print("FIELD VALUE CI width:", ci_w)
                self.ci_95_label.setText(f"95% CI: <span style='{_v}'>{ci_lo*100:.1f}–{ci_hi*100:.1f}%</span>")
                self.ci_width_label.setText(f"CI width: <span style='{_v}'>{ci_w:.1f}%</span>")
            else:
                self.ci_95_label.setText("95% CI: —")
                self.ci_width_label.setText("CI width: —")
        else:
            self.p_hit_value_label.setText("HIT %:")
            self.hits_value_label.setText("HITS:")
            self.stability_grade_label.setText("Stability:")
            self.sample_count_label.setText("Sample count:")
            self.cep50_value_label.setText("CEP50:")
            self.ci_95_label.setText("95% CI:")
            self.ci_width_label.setText("CI width:")

        # Impact plot
        self.mission_fig_op.clear()
        ax = self.mission_fig_op.add_subplot(1, 1, 1)
        wind_vec = snapshot.get("wind_vector")
        if wind_vec is not None and len(wind_vec) >= 2:
            wv = (float(wind_vec[0]), float(wind_vec[1]))
        else:
            wx = float(telem.get("wind_x", 0.0))
            wv = (wx, float(telem.get("wind_y", 0.0)))
        release_pt = (
            float(telem.get("x", 0.0)),
            float(telem.get("y", 0.0)),
        )
        tpos = snapshot.get("target_position")
        if tpos is not None and len(tpos) >= 2:
            tp = (float(tpos[0]), float(tpos[1]))
        else:
            tp = release_pt
        trad = float(snapshot.get("target_radius", 10.0) or 10.0)
        rseed = snapshot.get("random_seed")
        rseed = int(rseed) if rseed is not None else None
        mission_overview_tab_renderer.render(
            ax,
            decision=decision,
            target_hit_percentage=p_hit * 100.0,
            cep50=cep50,
            threshold=threshold,
            mode="Balanced",
            impact_points=impact_points,
            confidence_index=snapshot.get("confidence_index"),
            target_position=tp,
            target_radius=trad,
            advisory_result=advisory,
            release_point=release_pt,
            wind_vector=wv,
            dispersion_mode=self.current_mode if self.current_mode == "advanced" else "standard",
            view_zoom=1.0,
            snapshot_timestamp=(
                self._snapshot_created_at.strftime("%Y-%m-%d %H:%M:%S")
                if self._snapshot_created_at is not None
                else None
            ),
            random_seed=rseed,
            n_samples=n_samples,
        )
        self.mission_fig_op.subplots_adjust(left=0.09, right=0.99, top=0.97, bottom=0.08)
        self.mission_canvas_op.draw_idle()

        # Advisory column (doctrine reason when available, else advisory)
        decision_reason = snapshot.get("decision_reason")
        doctrine_desc = snapshot.get("doctrine_description")
        explorer_status = snapshot.get("explorer_status", "")
        if decision_upper == "DROP":
            if advisory:
                self.advisory_reason_label.setText(f"Reason: {advisory.current_feasibility}")
                self.advisory_stat_note_label.setText(f"Statistical note: {getattr(advisory, 'trend_summary', '—')}")
                suggested = getattr(advisory, "suggested_direction", "Hold Position")
                self.advisory_actions_label.setText(f"Actions: • {suggested}")
            elif decision_reason:
                self.advisory_reason_label.setText(f"Reason: {decision_reason}")
                self.advisory_stat_note_label.setText(f"Doctrine: {doctrine_desc or '—'}")
                self.advisory_actions_label.setText("Actions: —")
            else:
                self.advisory_reason_label.setText("Reason: —")
                self.advisory_stat_note_label.setText("Statistical note: —")
                self.advisory_actions_label.setText("Actions: —")
        elif explorer_status == "FEASIBLE_SHIFT_FOUND":
            shift_m = snapshot.get("suggested_shift_m", 0.0)
            time_s = snapshot.get("suggested_time_s", 0.0)
            self.advisory_reason_label.setText(
                f"NO DROP — Move forward {shift_m:.1f} m (~{time_s:.1f} s) to enter release window."
            )
            self.advisory_stat_note_label.setText("Statistical note: —")
            self.advisory_actions_label.setText("Actions: —")
        elif explorer_status == "OUT_OF_RANGE":
            self.advisory_reason_label.setText("NO DROP — Target out of geometric range.")
            self.advisory_stat_note_label.setText("Statistical note: —")
            self.advisory_actions_label.setText("Actions: —")
        elif advisory:
            self.advisory_reason_label.setText(f"Reason: {advisory.current_feasibility}")
            self.advisory_stat_note_label.setText(f"Statistical note: {getattr(advisory, 'trend_summary', '—')}")
            suggested = getattr(advisory, "suggested_direction", "Hold Position")
            self.advisory_actions_label.setText(f"Actions: • {suggested}")
        elif decision_reason:
            self.advisory_reason_label.setText(f"Reason: {decision_reason}")
            self.advisory_stat_note_label.setText(f"Doctrine: {doctrine_desc or '—'}")
            self.advisory_actions_label.setText("Actions: —")
        else:
            self.advisory_reason_label.setText("Reason: —")
            self.advisory_stat_note_label.setText("Statistical note: —")
            self.advisory_actions_label.setText("Actions: —")

        # Current Factors (from snapshot telemetry only)
        wind_x = float(telem.get("wind_x", 0.0))
        altitude = float(telem.get("z", 100.0))
        speed = float(telem.get("vx", 20.0))
        _val = "color:#e8e8e8"
        self.wind_label.setText(f"Wind: <span style='{_val}'>{wind_x:.2f} m/s</span>")
        self.altitude_label.setText(f"Altitude: <span style='{_val}'>{altitude:.0f} m</span>")
        self.speed_label.setText(f"Speed: <span style='{_val}'>{speed:.1f} m/s</span>")
        # AX-SENSITIVITY-HYBRID-09: Wind sensitivity (LIVE mode)
        sens_live = snapshot.get("sensitivity_live") or {}
        sens_str = sens_live.get("wind_sensitivity", "—")
        self.wind_sensitivity_label.setText(f"Wind Sensitivity: <span style='{_val}'>{sens_str}</span>")
        # AX-MISS-TOPOLOGY-HYBRID-12: Drift (LIVE mode)
        topo_live = snapshot.get("topology_live") or {}
        drift_str = topo_live.get("drift_axis", "—")
        self.drift_label.setText(f"Drift: <span style='{_val}'>{drift_str.title() if isinstance(drift_str, str) else str(drift_str)}</span>")
        # AX-RELEASE-CORRIDOR-19: Release corridor (LIVE mode)
        rc_live = snapshot.get("release_corridor_live") or {}
        rc_w = rc_live.get("corridor_width_m")
        if rc_w is None:
            rc_str = "—"
        elif isinstance(rc_w, str):
            rc_str = rc_w
        else:
            rc_str = f"{float(rc_w):.1f} m"
        self.release_corridor_label.setText(f"Release Corridor: <span style='{_val}'>{rc_str}</span>")

    def _apply_new_sim_card_style(self, hovered: bool = False) -> None:
        if hovered:
            border = "2px solid #2cff05"
            bg = "#0f1a0f"
            self._new_sim_icon.setStyleSheet("font-size: 29px; color: #2cff05; border: none; background: transparent;")
            self._new_sim_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #2cff05; border: none; background: transparent;")
            self._new_sim_subtitle.setStyleSheet("font-size: 13px; color: #2cff05; border: none; background: transparent;")
            glow = QGraphicsDropShadowEffect(self.new_sim_card)
            glow.setBlurRadius(14)
            glow.setColor(QColor(44, 255, 5, 90))
            glow.setOffset(0, 0)
            self.new_sim_card.setGraphicsEffect(glow)
        else:
            border = "1px solid #1a2a1a"
            bg = "#0d140d"
            self._new_sim_icon.setStyleSheet("font-size: 28px; color: #2cff05; border: none; background: transparent;")
            self._new_sim_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2cff05; border: none; background: transparent;")
            self._new_sim_subtitle.setStyleSheet("font-size: 12px; color: #2cff05; border: none; background: transparent;")
            self.new_sim_card.setGraphicsEffect(None)
        self.new_sim_card.setStyleSheet(
            f"QFrame#newSimCard {{ border: {border}; border-radius: 6px; background-color: {bg}; }}"
        )

    def _on_run_once_clicked(self) -> None:
        """AX-EXECUTION-MODE-HYBRID-07: Run single simulation in MANUAL mode."""
        if self.simulation_running:
            return
        if not self._is_mission_ready():
            self._show_warning("Configure payload before running simulation.")
            return
        self._execution_mode = "MANUAL"
        self._live_timer.stop()
        self._start_simulation(trigger="run_once")

    def _on_live_clicked(self) -> None:
        """AX-EXECUTION-MODE-HYBRID-07: Toggle LIVE / MANUAL execution mode."""
        if self._execution_mode == "MANUAL":
            if not self._is_mission_ready():
                self._show_warning("Configure payload before running simulation.")
                return
            self._execution_mode = "LIVE"
            self.system_mode = "LIVE"
            self.mission_config_tab.apply_system_mode("LIVE")
            self.auto_evaluate_paused = False
            self._apply_live_telemetry_to_config()
            self._push_config_to_worker()
            self._start_evaluation_worker()
            self.live_btn.setText("STOP")
            self.live_btn.setStyleSheet("font-size: 14px; color: white; background-color: #cc3333;")
            self.run_once_btn.setEnabled(False)
            self.live_mode_label.setText("LIVE MODE ACTIVE")
            self.live_mode_label.show()
            self._live_timer.start()
        else:
            self._execution_mode = "MANUAL"
            self.system_mode = "SNAPSHOT"
            self.mission_config_tab.apply_system_mode("SNAPSHOT")
            self.auto_timer.stop()
            self._stop_evaluation_worker()
            self.live_btn.setText("LIVE")
            self.live_btn.setStyleSheet("font-size: 14px; color: #22cc22;")
            self.run_once_btn.setEnabled(True)
            self.live_mode_label.hide()
            self._live_timer.stop()

    def _auto_evaluate(self) -> None:
        """AX-EXECUTION-MODE-HYBRID-07: Timer callback for LIVE mode — start simulation when idle."""
        if self._execution_mode != "LIVE":
            return
        if self.simulation_running:
            return
        self._start_simulation(trigger="live_timer")

    def _on_new_simulation_clicked(self) -> None:
        """Unlock current snapshot and navigate to Mission Config for reconfiguration."""
        self.snapshot_active = False
        self.app_state = AppState.PAYLOAD_SELECTED
        # Preserve threshold_pct from config_state (packet-driven invariant; no UI read during render)
        with self.config_state.lock:
            th = float(self.config_state.data.get("threshold_pct", 75.0))
        self._latest_snapshot = build_config_snapshot(th)
        self._snapshot_created_at = None
        self.current_snapshot_id = None
        self._update_app_state_ui()
        self.main_tabs.setCurrentWidget(self.payload_tab)
        self.status_strip.snapshot_label.setText("Snapshot ID: --- | New Simulation — Configure & Run")
        self._render_mission_tab()

    def _resolve_n_samples(self, snapshot: dict | None) -> int:
        """Resolve sample count from snapshot or config_state (single source of truth)."""
        if snapshot and snapshot.get("n_samples") is not None:
            return int(snapshot["n_samples"])
        with self.config_state.lock:
            return int(self.config_state.data.get("n_samples", 1000))

    def _render_analysis_tab(self) -> None:
        snapshot = self._latest_snapshot or {}
        impact_points = snapshot.get("impact_points", [])
        p_hit = float(snapshot.get("P_hit", 0.0) or 0.0)
        cep50 = float(snapshot.get("cep50", 0.0) or 0.0)
        with self.config_state.lock:
            cfg = dict(self.config_state.data)
        target_pos = snapshot.get("target_position") or (cfg.get("target_x", 0.0), cfg.get("target_y", 0.0), cfg.get("target_elevation", 0.0))
        target_rad = float(snapshot.get("target_radius") or cfg.get("target_radius", 5.0) or 5.0)
        uav_pos = (float(cfg.get("uav_x", 0.0)), float(cfg.get("uav_y", 0.0)), float(cfg.get("uav_altitude", 100.0)))
        wind_x = float(cfg.get("wind_x", 2.0))
        random_seed_val = int(cfg.get("random_seed", 42))

        self.analysis_fig.clear()
        ax = self.analysis_fig.add_subplot(1, 1, 1)
        analysis_tab_renderer.render(
            ax,
            impact_points=impact_points,
            target_position=target_pos,
            target_radius=target_rad,
            uav_position=uav_pos,
            wind_mean=(wind_x, 0.0, 0.0),
            cep50=cep50,
            target_hit_percentage=p_hit * 100.0,
            impact_velocity_stats=snapshot.get("impact_velocity_stats"),
            max_safe_impact_speed=None,
            sensitivity_matrix=snapshot.get("sensitivity_matrix"),
            dominant_risk_factor=snapshot.get("dominant_risk_factor"),
            topology_matrix=snapshot.get("topology_matrix"),
            release_corridor_matrix=snapshot.get("release_corridor_matrix"),
            fragility_state=snapshot.get("fragility_state"),
            uncertainty_contribution=snapshot.get("uncertainty_contribution"),
            prob_vs_distance=snapshot.get("prob_vs_distance"),
            prob_vs_wind_uncertainty=snapshot.get("prob_vs_wind_uncertainty"),
            dispersion_mode=self.current_mode,
            view_zoom=1.0,
            snapshot_timestamp=(
                self._snapshot_created_at.strftime("%Y-%m-%d %H:%M:%S")
                if self._snapshot_created_at is not None
                else None
            ),
            random_seed=random_seed_val,
            n_samples=self._resolve_n_samples(snapshot),
            ci_low=snapshot.get("ci_low"),
            ci_high=snapshot.get("ci_high"),
            threshold_pct=snapshot.get("threshold_pct"),
            p_hit=p_hit,
        )
        try:
            self.analysis_fig.subplots_adjust(left=0, right=1, bottom=0, top=1, wspace=0, hspace=0)
        except Exception:
            pass
        self.analysis_canvas.draw_idle()

    def _render_payload_tab(self) -> None:
        """Mission Config tab: form-based; no canvas render."""
        pass

    def _render_sensor_tab(self) -> None:
        self.telemetry_fig.clear()
        ax = self.telemetry_fig.add_subplot(1, 1, 1)
        telem = self._last_telemetry or {}
        with self.config_state.lock:
            cfg = dict(self.config_state.data)
        wind_x = float(telem.get("wind_x", cfg.get("wind_x", 2.0)))
        wind_std = float(telem.get("wind_std", cfg.get("wind_std", 0.8)))
        uav_alt = float(telem.get("z", cfg.get("uav_altitude", 100.0)))
        uav_vx = float(telem.get("vx", cfg.get("uav_vx", 20.0)))
        telem_age = float(self._last_telemetry.get("age_s", 0.0) or 0.0)
        telem_status = str(self._last_telemetry.get("status", "Fresh"))
        wind_speed = abs(wind_x)
        wind_dir = 0.0 if wind_x >= 0 else 180.0
        wind_conf = "High" if telem_status == "Fresh" else ("Medium" if telem_status == "Delay" else "Low")
        source = "Telemetry" if self.system_mode == "LIVE" else "Assumed Gaussian"

        sensor_telemetry.render(
            ax,
            gnss_speed_ms=uav_vx,
            gnss_heading_deg=0.0,
            gnss_altitude_m=uav_alt,
            gnss_fix="3D Fix",
            gnss_freshness_s=telem_age if self.system_mode == "LIVE" else None,
            wind_dir_deg=wind_dir,
            wind_speed_ms=wind_speed,
            wind_uncertainty=wind_std,
            wind_source=source,
            wind_confidence=wind_conf,
            wind_mean_ms=wind_x,
            wind_std_dev_ms=wind_std,
            telemetry_live=(self.system_mode == "LIVE"),
        )
        try:
            self.telemetry_fig.tight_layout()
        except Exception:
            pass
        self.telemetry_canvas.draw_idle()

    def _render_system_tab(self) -> None:
        from configs import mission_configs as cfg

        self.system_fig.clear()
        ax = self.system_fig.add_subplot(1, 1, 1)
        warnings = ["No active warnings."]
        if self.system_mode == "LIVE" and self.auto_evaluate_paused:
            warnings = ["Auto-evaluate paused due to performance threshold (>1.5s run)."]
        snapshot = self._latest_snapshot or {}
        with self.config_state.lock:
            cfg_data = dict(self.config_state.data)
        system_status.render(
            ax,
            random_seed=int(cfg_data.get("random_seed", cfg.RANDOM_SEED)),
            n_samples=self._resolve_n_samples(snapshot),
            dt=float(cfg.dt) if hasattr(cfg, "dt") else 0.01,
            snapshot_created_at=self._snapshot_created_at,
            warnings=warnings,
        )
        try:
            self.system_fig.tight_layout()
        except Exception:
            pass
        self.system_canvas.draw_idle()

    def _update_system_state_from_snapshot(self, snapshot: dict) -> None:
        if not snapshot:
            return
        with self.system_state.lock:
            target_pos = snapshot.get("target_position")
            if target_pos is not None:
                self.system_state.target_position = target_pos
            if "target_radius" in snapshot:
                tr = snapshot.get("target_radius")
                self.system_state.settings["target_radius"] = tr
                self.system_state.target_radius = tr

            decision = snapshot.get("decision")
            status = None
            if isinstance(decision, str):
                status = "DROP NOW" if decision.strip().upper() == "DROP" else "NO DROP"
            guidance = {
                "status": status,
                "P_hit": snapshot.get("P_hit"),
                "threshold": snapshot.get("threshold_pct"),
                "target_release_point": snapshot.get("target_release_point"),
                "uncertainty_contribution": snapshot.get("uncertainty_contribution"),
            }
            self.system_state.guidance_result = guidance
            if "threshold_pct" in snapshot:
                self.system_state.threshold = snapshot.get("threshold_pct")

            envelope = {}
            if "feasible_offsets" in snapshot:
                envelope["feasible_offsets"] = snapshot.get("feasible_offsets")
            if "impact_mean" in snapshot:
                envelope["impact_mean"] = snapshot.get("impact_mean")
            if "impact_cov" in snapshot:
                envelope["impact_cov"] = snapshot.get("impact_cov")
            if "target_radius" in snapshot:
                envelope["target_radius"] = snapshot.get("target_radius")
            if envelope:
                self.system_state.envelope_result = envelope

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #0d140d;
            }
            QWidget {
                color: #86a886;
                background-color: #0d140d;
                font-family: Consolas, "Courier New", monospace;
                font-size: 12px;
            }
            QScrollArea {
                background-color: #0d140d;
                border: none;
            }
            QScrollBar:vertical {
                background: #0d140d;
                width: 8px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #1a2a1a;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QFrame#plotPlaceholder, QFrame#statusStrip,
            QFrame#decisionCardInputs, QFrame#decisionStateCard, QFrame#decisionCardStats,
            QFrame#advisoryColumn {
                background-color: #0a110a;
                border: 1px solid #1c2d1c;
                border-radius: 4px;
            }
            QLabel#decisionLabel {
                font-size: 32px;
                font-weight: bold;
                color: #6aaf6a;
                padding: 8px;
            }
            QLabel#pausedMessage {
                color: #d4a017;
                font-size: 11px;
            }
            QLabel#panelTitle {
                color: #2cff05;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QFrame#configGroup {
                background-color: #0d140d;
                border: 1px solid #1e2f1e;
                border-radius: 4px;
            }
            QLabel#groupTitle {
                color: #2cff05;
                font-weight: bold;
            }
            QLabel#panelFieldValue {
                color: #6c8f6a;
            }
            QLabel#advisoryFieldHighlight {
                color: #5ed85e;
                font-size: 14px;
            }
            QLabel#decisionFieldHighlight {
                color: #5ed85e;
                font-size: 14px;
            }
            QLabel#panelSubtitle, QLabel#placeholderLine, QLabel#statusLabel {
                color: #6c8f6a;
            }
            QLabel#plotPlaceholderText {
                color: #6c8f6a;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton, QAbstractSpinBox, QComboBox {
                min-height: 34px;
                padding: 6px 12px;
                background-color: #0b120b;
                color: #6c8f6a;
                border: 1px solid #1a2a1a;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:hover, QAbstractSpinBox:hover, QComboBox:hover {
                border: 1px solid #2f4a2f;
            }
            QComboBox QAbstractItemView {
                background: #0b120b;
                color: #6c8f6a;
                selection-background-color: #133013;
            }
            QTabWidget#mainTabs::pane {
                border: none;
                background: #0d140d;
                margin-top: 0;
            }
            QTabWidget#mainTabs::tab-bar {
                alignment: center;
            }
            QTabBar::tab {
                min-width: 140px;
                min-height: 36px;
                padding: 8px 14px;
                margin-right: 6px;
                background-color: #0d140d;
                color: #6c8f6a;
                border: 1px solid #1a2a1a;
                border-radius: 6px;
                border-bottom: 1px solid #1a2a1a;
                font-weight: normal;
                font-size: 15px;
            }
            QTabBar::tab:selected {
                background-color: #0d140d;
                color: #2cff05;
                font-size: 15px;
                border: 1px solid #2cff05;
                border-radius: 6px;
                border-bottom: 1px solid #2cff05;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background-color: #132013;
            }
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
                width: 14px;
                border: none;
                background: #122012;
            }
            QPushButton:checked {
                color: #2cff05;
                border: 2px solid #2cff05;
                background-color: rgba(44, 255, 5, 0.08);
                font-weight: bold;
            }
            """
        )

    def _set_mode(self, mode: str) -> None:
        """Change UI mode (standard/advanced) - only affects UI density, does not reset engine/snapshot."""
        self.current_mode = mode
        self._refresh_mode_buttons()
        self._switch_mission_tab_layout()
        # Re-initialize app state if switching to Standard Mode
        if mode == "standard":
            if self.app_state == AppState.NO_PAYLOAD:
                self._initialize_app_state()
            else:
                self._update_app_state_ui()
        else:
            pass
        self._render_mission_tab()
        self._render_analysis_tab()
        if self.snapshot_active:
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id or '---'} | Locked | Mode: {mode.title()}"
            )
        else:
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id or '---'} | Editable | Mode: {mode.title()}"
            )

    def _switch_mission_tab_layout(self) -> None:
        """No tab swap — Tactical Map always uses operator layout. Mode only changes plot rendering."""
        pass

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change."""
        pass

    def _switch_to_tab(self, index: int) -> None:
        self.main_tabs.setCurrentIndex(index)

    def _refresh_mode_buttons(self) -> None:
        if not hasattr(self, 'operator_btn') or not hasattr(self, 'engineering_btn'):
            return  # Buttons not yet created
        is_standard = self.current_mode == "standard"
        self.operator_btn.setChecked(is_standard)
        self.engineering_btn.setChecked(not is_standard)

    def _initialize_app_state(self) -> None:
        """Initialize application state on startup (Standard Mode only)."""
        if self.current_mode == "standard":
            self.app_state = AppState.NO_PAYLOAD
            self._update_app_state_ui()
            # Default tab index 0 is Tactical Map
            self.main_tabs.setCurrentIndex(0)  # Tactical Map is first tab

    def _update_app_state_ui(self) -> None:
        """Update UI based on current application state (Standard Mode only)."""
        if self.current_mode != "standard":
            return
        if self.app_state == AppState.PAYLOAD_SELECTED:
            if hasattr(self, 'invalidation_label'):
                self.invalidation_label.hide()
        elif self.app_state == AppState.EVALUATED:
            if hasattr(self, 'invalidation_label'):
                self.invalidation_label.hide()
        elif self.app_state == AppState.INVALIDATED:
            if hasattr(self, 'invalidation_label'):
                self.invalidation_label.show()
            self._disable_decision_display()

    def _reset_decision_display(self) -> None:
        """Reset decision display — triggers a fresh render which will resolve to READY or PAUSED."""
        self._render_mission_tab()

    def _disable_decision_display(self) -> None:
        """Disable decision result display — re-render resolves to PAUSED."""
        self._render_mission_tab()

    def _update_evaluate_button_text(self) -> None:
        # Evaluate button removed from header - method kept for compatibility but does nothing
        pass

    @Slot(float)
    def _on_threshold_changed(self, _value: float) -> None:
        self._render_mission_tab()

    def _on_target_radius_slider_changed(self, value: int) -> None:
        val = 0.5 + (value - 1) * 0.5
        self.target_radius_spinbox.blockSignals(True)
        self.target_radius_spinbox.setValue(val)
        self.target_radius_spinbox.blockSignals(False)
        with self.config_state.lock:
            self.config_state.data["target_radius"] = val
        self._render_mission_tab()

    def _on_target_radius_spinbox_changed(self, value: float) -> None:
        self.target_radius_slider.blockSignals(True)
        self.target_radius_slider.setValue(int((value - 0.5) / 0.5) + 1)
        self.target_radius_slider.blockSignals(False)
        with self.config_state.lock:
            self.config_state.data["target_radius"] = value
        self._render_mission_tab()

    @Slot(str)
    def _on_auto_eval_changed(self, value: str) -> None:
        if self.system_mode != "LIVE":
            self.auto_timer.stop()
            return
        value_norm = str(value).strip().upper()
        self._auto_eval_interval = value_norm
        if value_norm == "OFF":
            self.auto_timer.stop()
            self.auto_evaluate_paused = False
            return
        if value_norm == "1S":
            self.auto_timer.start(1000)
            return
        if value_norm == "2S":
            self.auto_timer.start(2000)

    @Slot()
    def auto_evaluate(self) -> None:
        if self.system_mode != "LIVE":
            return
        if self.auto_evaluate_paused:
            return
        if not self.snapshot_active:
            return
        if self.simulation_running:
            return
        self._update_simulation_age()
        self._start_simulation(trigger="auto")

    def _on_evaluate_clicked(self) -> None:
        # No restrictions - allow evaluate anytime
        
        if self.simulation_running:
            self.status_strip.snapshot_label.setText("Snapshot: Simulation already running...")
            return

        if not self.snapshot_active:
            if self.system_mode == "LIVE":
                self.status_strip.snapshot_label.setText("Snapshot: Evaluating with live telemetry...")
            else:
                self.status_strip.snapshot_label.setText("Snapshot: Evaluating...")
            self._start_simulation(trigger="manual_lock")
            return

        # Unlock only; do not run simulation.
        self.snapshot_active = False
        self._update_evaluate_button_text()
        self.status_strip.snapshot_label.setText(
            f"Snapshot ID: {self.current_snapshot_id or '---'} | Unlocked | Modify and Evaluate"
        )

    def _start_simulation(self, trigger: str) -> None:
        if self.simulation_running:
            return
        if not self._is_mission_ready():
            self._show_warning("Configure payload before running simulation.")
            return
        # --- PHASE 6: State hash check before simulation ---
        with self.config_state.lock:
            config_state = dict(self.config_state.data)
        print("CONFIG HASH:", hash(str(config_state)))
        print("EVAL HASH:", hash(str(None)))
        print("SNAPSHOT HASH:", hash(str(self._latest_snapshot or {})))
        self.simulation_running = True
        self._simulation_started_at = time.time()
        self._push_config_to_worker()  # Ensure config_state is current before run
        with self.config_state.lock:
            cfg = dict(self.config_state.data)
        worker = SimulationWorker(cfg, trigger, self)
        worker.simulation_done.connect(self._on_simulation_done)
        worker.simulation_failed.connect(self._on_simulation_failed)
        worker.finished.connect(self._on_simulation_finished)
        self._simulation_worker = worker
        print("[WORKER TRACE] SimulationWorker started")
        worker.start()

    @Slot(dict, str)
    def _on_simulation_done(self, snapshot: dict, trigger: str) -> None:
        # AX-SENSITIVITY-STABILITY-AUDIT-10: performance log
        print("compute_time_ms:", snapshot.get("compute_time_ms"))
        t0 = time.perf_counter()
        last_snapshot = self._latest_snapshot or {}
        previous_decision = last_snapshot.get("decision") if last_snapshot.get("snapshot_type") == "EVALUATION" else None
        snap = dict(snapshot or {})
        snap["snapshot_type"] = "EVALUATION"
        snap.setdefault("compute_time_ms", None)
        enrich_evaluation_snapshot(snap, previous_decision)
        with self.config_state.lock:
            snap["mission_mode"] = self.config_state.data.get("mission_mode", "TACTICAL")
        self._latest_snapshot = snap
        self._update_system_state_from_snapshot(snap)
        self._snapshot_created_at = datetime.now()
        self.current_snapshot_id = self._snapshot_created_at.strftime("AX-%Y%m%d-%H%M%S")
        self._last_eval_time = time.time()
        run_duration_sec = None
        if self._simulation_started_at is not None:
            run_duration_sec = max(0.0, self._last_eval_time - self._simulation_started_at)
        self._update_simulation_age()
        
        # Update application state (Standard Mode only)
        if self.current_mode == "standard" and trigger == "manual_lock":
            self.app_state = AppState.EVALUATED
            self._update_app_state_ui()
        
        self._render_mission_tab()
        self._render_analysis_tab()
        self._render_system_tab()
        snap["render_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

        if (
            run_duration_sec is not None
            and run_duration_sec > 1.5
            and self.system_mode == "LIVE"
            and str(self._live_auto_eval_combo.currentText()).strip().upper() != "OFF"
        ):
            self.auto_evaluate_paused = True
            self.auto_timer.stop()

        if trigger == "manual_lock":
            self.snapshot_active = True
            self._update_evaluate_button_text()
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id} | Locked | P_hit: {float(snapshot.get('P_hit', 0.0)) * 100.0:.1f}%"
            )
            return

        if trigger == "auto":
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id} | Auto-updated | P_hit: {float(snapshot.get('P_hit', 0.0)) * 100.0:.1f}%"
            )
            return

        if trigger == "live_timer":
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id} | LIVE | P_hit: {float(snapshot.get('P_hit', 0.0)) * 100.0:.1f}%"
            )
            return

        if trigger == "run_once":
            self.status_strip.snapshot_label.setText(
                f"Snapshot ID: {self.current_snapshot_id} | Run Once | P_hit: {float(snapshot.get('P_hit', 0.0)) * 100.0:.1f}%"
            )
            return

        self.status_strip.snapshot_label.setText(
            f"Snapshot ID: {self.current_snapshot_id} | Updated"
        )

    @Slot(str, str)
    def _on_simulation_failed(self, error: str, trigger: str) -> None:  # noqa: ARG002
        short = (error or "unknown error").strip()
        self._latest_snapshot = {"snapshot_type": "ERROR", "error_message": short[:200]}
        self._log_state_transition("ERROR")
        self.status_strip.snapshot_label.setText(f"Snapshot: Failed ({short[:72]})")
        self._render_mission_tab()

    @Slot()
    def _on_simulation_finished(self) -> None:
        self.simulation_running = False
        self._simulation_started_at = None
        if self._simulation_worker is not None:
            self._simulation_worker.deleteLater()
            self._simulation_worker = None

    def _start_telemetry(self, source: str = "mock", file_path: str | None = None) -> None:
        if self.telemetry_worker is not None:
            self.telemetry_worker.stop()
            self.telemetry_worker.wait(1500)
            self.telemetry_worker = None
        self.telemetry_worker = TelemetryWorker(self, source=source, file_path=file_path or None)
        self.telemetry_worker.telemetry_updated.connect(self.handle_telemetry)
        self.telemetry_worker.start()
        self.status_strip.telemetry_label.setText("Telemetry: LIVE")

    def _on_telemetry_source_apply(self) -> None:
        source = self._live_telemetry_combo.currentData() or "mock"
        path = self._live_telemetry_path.text().strip() or None
        self._start_telemetry(source=str(source), file_path=path)
        self.status_strip.telemetry_label.setText(
            "Telemetry: LIVE (file)" if source == "file" and path else "Telemetry: LIVE"
        )

    def _update_simulation_age(self) -> None:
        pass  # No side panel to update

    def _apply_live_telemetry_to_config(self) -> None:
        """Push live telemetry into config_state for simulation."""
        data = self._last_telemetry or {}
        if not data:
            return
        with self.config_state.lock:
            self.config_state.data["uav_x"] = float(data.get("x", self.config_state.data.get("uav_x", 0.0)))
            self.config_state.data["uav_y"] = float(data.get("y", self.config_state.data.get("uav_y", 0.0)))
            self.config_state.data["uav_altitude"] = float(data.get("z", self.config_state.data.get("uav_altitude", 100.0)))
            self.config_state.data["uav_vx"] = float(data.get("vx", self.config_state.data.get("uav_vx", 20.0)))
            self.config_state.data["wind_x"] = float(data.get("wind_x", self.config_state.data.get("wind_x", 2.0)))
            self.config_state.data["wind_std"] = float(data.get("wind_std", self.config_state.data.get("wind_std", 0.8)))

    def _seed_config_state(self) -> None:
        """Seed config_state with defaults from mission_configs. Called once at init."""
        from configs import mission_configs as cfg
        with self.config_state.lock:
            self.config_state.data = {
                "mass": float(cfg.mass),
                "cd": float(cfg.Cd),
                "area": float(cfg.A),
                "uav_x": float(cfg.uav_pos[0]),
                "uav_y": float(cfg.uav_pos[1]),
                "uav_altitude": float(cfg.uav_pos[2]),
                "uav_vx": float(cfg.uav_vel[0]),
                "uav_vy": float(cfg.uav_vel[1]) if len(cfg.uav_vel) > 1 else 0.0,
                "target_x": float(cfg.target_pos[0]),
                "target_y": float(cfg.target_pos[1]),
                "target_elevation": float(cfg.target_pos[2]) if len(cfg.target_pos) >= 3 else 0.0,
                "target_radius": float(cfg.target_radius),
                "wind_x": float(cfg.wind_mean[0]),
                "wind_std": float(cfg.wind_std),
                "n_samples": int(self._mission_config_overrides.get("n_samples", 1000)),
                "random_seed": int(self._mission_config_overrides.get("random_seed", cfg.RANDOM_SEED)),
                "threshold_pct": float(cfg.THRESHOLD_SLIDER_INIT),
                "doctrine_mode": str(self._mission_config_overrides.get("doctrine_mode", "BALANCED")),
                "mission_mode": str(self._mission_config_overrides.get("mission_mode", "TACTICAL")),
                "simulation_fidelity": "advanced",
            }

    def _push_config_to_worker(self) -> None:
        """Push config to worker. Uses config_state as base; MissionConfigTab overrides on commit."""
        with self.config_state.lock:
            cfg = dict(self.config_state.data)
        cfg.update(self._mission_config_overrides)
        cfg["prev_wind_gradient"] = self._prev_wind_gradient
        cfg["mission_mode"] = str(cfg.get("mission_mode", "TACTICAL")).strip().upper()
        if cfg["mission_mode"] not in ("TACTICAL", "HUMANITARIAN"):
            cfg["mission_mode"] = "TACTICAL"
        cfg["doctrine_mode"] = str(cfg.get("doctrine_mode", "BALANCED")).strip().upper()
        cfg["n_samples"] = int(cfg.get("n_samples", 1000))
        cfg["random_seed"] = int(cfg.get("random_seed", 42))
        cfg["mass"] = float(cfg.get("mass", 1.0))
        cfg["cd"] = float(cfg.get("cd", 0.47))
        cfg["area"] = float(cfg.get("area", 0.01))
        with self.config_state.lock:
            self.config_state.data = dict(cfg)

    def _start_evaluation_worker(self) -> None:
        """Start continuous evaluation worker (LIVE mode)."""
        if self.evaluation_worker is None:
            return
        if self.evaluation_worker.isRunning():
            return
        self.evaluation_worker.running = True
        print("[WORKER TRACE] EvaluationWorker started")
        self.evaluation_worker.start()

    def _stop_evaluation_worker(self) -> None:
        """Stop continuous evaluation worker."""
        if self.evaluation_worker is None:
            return
        self.evaluation_worker.running = False
        self.evaluation_worker.wait(2000)

    @Slot(dict)
    def _handle_evaluation_result(self, data: dict) -> None:
        """Atomic UI update from evaluation worker result."""
        if self.system_mode != "LIVE":
            return
        impact_points = data.get("impact_points", [])
        p_hit = float(data.get("P_hit", 0.0) or 0.0)
        cep50 = float(data.get("cep50", 0.0) or 0.0)
        decision = str(data.get("decision", "NO DROP"))
        n_samples = int(data.get("n_samples", 1000))
        threshold = float(data.get("threshold_pct", 75.0))

        snapshot = {
            "snapshot_type": data.get("snapshot_type", "EVALUATION"),
            "impact_points": impact_points,
            "hits": data.get("hits"),
            "P_hit": p_hit,
            "cep50": cep50,
            "target_position": data.get("target_position"),
            "target_radius": data.get("target_radius"),
            "confidence_index": data.get("confidence_index"),
            "n_samples": n_samples,
            "telemetry": data.get("telemetry_snapshot", {}),
            "wind_vector": data.get("wind_vector"),
            "random_seed": data.get("random_seed"),
            "threshold_pct": threshold,
            "ci_low": data.get("ci_low"),
            "ci_high": data.get("ci_high"),
            "p_hat": data.get("p_hat"),
            "decision": data.get("decision"),
            "mission_mode": data.get("mission_mode", "TACTICAL"),
            "decision_reason": data.get("decision_reason"),
            "doctrine_mode": data.get("doctrine_mode"),
            "doctrine_description": data.get("doctrine_description"),
        }
        if "sensitivity_live" in data:
            snapshot["sensitivity_live"] = data["sensitivity_live"]
        if "topology_live" in data:
            snapshot["topology_live"] = data["topology_live"]
        if "release_corridor_live" in data:
            snapshot["release_corridor_live"] = data["release_corridor_live"]
        if "fragility_state" in data:
            snapshot["fragility_state"] = data["fragility_state"]
        last_snapshot = self._latest_snapshot or {}
        previous_decision = last_snapshot.get("decision") if last_snapshot.get("snapshot_type") == "EVALUATION" else None
        enrich_evaluation_snapshot(snapshot, previous_decision)
        t0 = time.perf_counter()
        self._prev_wind_gradient = data.get("updated_wind_gradient")
        self._push_config_to_worker()
        self._latest_snapshot = snapshot
        self._update_system_state_from_snapshot(snapshot)
        self._log_state_transition("EVALUATION")
        self.app_state = AppState.EVALUATED
        self.snapshot_active = True
        self._last_eval_time = data.get("timestamp")
        # Timestamp from evaluation packet only—no drift from previous manual snapshot
        ts = data.get("timestamp")
        self._snapshot_created_at = datetime.fromtimestamp(ts) if ts is not None else None

        paused_info = None
        if self._glow_timer.isActive():
            self._glow_timer.stop()
        decision = str(snapshot.get("decision", "")).strip().upper()
        robustness = snapshot.get("robustness_status") or ""
        self._render_mission_tab_operator(
            snapshot, decision, p_hit, cep50, threshold, None, impact_points, paused_info,
            robustness_status=robustness,
        )
        snapshot["render_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        self._update_evaluate_button_text()

    @Slot(dict)
    def handle_telemetry(self, data: dict) -> None:
        self._last_telemetry = dict(data or {})
        with self.telemetry_state.lock:
            self.telemetry_state.data = dict(self._last_telemetry)
        with self.system_state.lock:
            x = float(self._last_telemetry.get("x", 0.0) or 0.0)
            y = float(self._last_telemetry.get("y", 0.0) or 0.0)
            vx = float(self._last_telemetry.get("vx", 0.0) or 0.0)
            vy = float(self._last_telemetry.get("vy", 0.0) or 0.0)
            self.system_state.vehicle_state = {
                "position": (x, y),
                "velocity": (vx, vy),
            }
        if self.system_mode == "LIVE":
            self._apply_live_telemetry_to_config()
            self._push_config_to_worker()
        packet_rate = float(data.get("packet_rate_hz", 2.0))
        age_s = float(data.get("age_s", 0.5))
        status = str(data.get("status", "LIVE"))
        self._update_simulation_age()
        if self.main_tabs.currentIndex() == self.main_tabs.indexOf(self.telemetry_tab):
            self._render_sensor_tab()
        self.status_strip.telemetry_label.setText("Telemetry: LIVE")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # READY block (card + label): click starts simulation. No hover/animation.
        if obj in (self.decision_state_card, self.decision_label) and getattr(self, "_current_decision", "") == "READY":
            if event.type() == QEvent.Type.MouseButtonPress:
                if not self.simulation_running and self.app_state == AppState.PAYLOAD_SELECTED:
                    self._start_simulation(trigger="manual_lock")
                return True
            return False
        # Paused message: READY -> click starts sim (no hover). PAUSED -> click navigates, hover zooms.
        if obj is self.paused_message_label:
            if event.type() == QEvent.Type.MouseButtonPress:
                if getattr(self, "_current_decision", "") == "READY":
                    if not self.simulation_running and self.app_state == AppState.PAYLOAD_SELECTED:
                        self._start_simulation(trigger="manual_lock")
                    return True
                if self._paused_target_tab is not None:
                    idx = self.main_tabs.indexOf(self._paused_target_tab)
                    if idx >= 0:
                        self.main_tabs.setCurrentIndex(idx)
                    return True
            if getattr(self, "_current_decision", "") == "PAUSED":
                if event.type() == QEvent.Type.Enter:
                    current = self.paused_message_label.font()
                    new_size = max(8.0, current.pointSizeF() + 1.0)
                    current.setPointSizeF(new_size)
                    self.paused_message_label.setFont(current)
                    self.paused_message_label.setStyleSheet("color: #f0f0f0; font-size: 12px; padding: 4px;")
                    return False
                if event.type() == QEvent.Type.Leave:
                    current = self.paused_message_label.font()
                    new_size = max(8.0, current.pointSizeF() - 1.0)
                    current.setPointSizeF(new_size)
                    self.paused_message_label.setFont(current)
                    self.paused_message_label.setStyleSheet("color: #d4a017; font-size: 11px; padding: 4px;")
                    return False
            return False
        # New Simulation card: click, hover glow + 1px zoom (card + children)
        _new_sim_widgets = (
            getattr(self, 'new_sim_card', None),
            getattr(self, '_new_sim_icon', None),
            getattr(self, '_new_sim_title', None),
            getattr(self, '_new_sim_subtitle', None),
        )
        if obj in _new_sim_widgets and obj is not None:
            if event.type() == QEvent.Type.MouseButtonPress:
                self._on_new_simulation_clicked()
                return True
            if event.type() == QEvent.Type.Enter:
                self._apply_new_sim_card_style(hovered=True)
                return False
            if event.type() == QEvent.Type.Leave:
                w = QApplication.widgetAt(QCursor.pos())
                while w and w is not self.new_sim_card:
                    w = w.parentWidget()
                if w is not self.new_sim_card:
                    self._apply_new_sim_card_style(hovered=False)
                return False
        # Canvas: forward wheel events to scroll area so page scrolls over the plot
        if hasattr(self, 'mission_canvas_op') and obj is self.mission_canvas_op:
            if event.type() == QEvent.Type.Wheel:
                scroll_area = self.mission_tab_operator.findChild(QScrollArea)
                if scroll_area is not None:
                    from PySide6.QtCore import QCoreApplication
                    QCoreApplication.sendEvent(scroll_area.viewport(), event)
                    return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.auto_timer.stop()
        if self._simulation_worker is not None and self._simulation_worker.isRunning():
            self._simulation_worker.wait(3000)

        if self.evaluation_worker is not None:
            self.evaluation_worker.running = False
            self.evaluation_worker.wait(2000)

        if self.telemetry_worker is not None:
            self.telemetry_worker.stop()
            self.telemetry_worker.wait(1500)
        self.status_strip.telemetry_label.setText("Telemetry: STOPPED")
        super().closeEvent(event)
