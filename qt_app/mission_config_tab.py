"""
Mission Config tab widget: horizontal console-style panels.
Mission Mode strip, Payload/Evaluation/Policy panels, Commit.
Reads from config snapshot for display; commits push to config_state.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from widgets import NoWheelDoubleSpinBox, NoWheelSlider, NoWheelSpinBox

from product.ui.tabs.payload_library import PAYLOAD_LIBRARY, CATEGORIES, get_default_physics_for_payload
from src.decision_doctrine import DOCTRINE_DESCRIPTIONS


# Typography
PRIMARY_COLOR = "#2cff05"
SECONDARY_COLOR = "#7cff9b"
INPUT_STYLE = "color: #e8e8e8; background-color: #141814; border: 1px solid #2a3a2a; min-height: 31px; padding: 4px 10px;"
BTN_STYLE = f"color: {PRIMARY_COLOR}; background-color: #141814; border: 1px solid #2a3a2a; min-height: 31px; padding: 4px 10px;"

# Humanitarian label mapping (display only; internals stay STRICT/BALANCED/AGGRESSIVE)
DOCTRINE_DISPLAY_LABELS_HUMANITARIAN = {
    "STRICT": "Maximum Safety",
    "BALANCED": "Standard Safety",
    "AGGRESSIVE": "Time Sensitive",
}

MISSION_MODES = ("TACTICAL", "HUMANITARIAN")
FIDELITY_VALUES = ("standard", "advanced")
DOCTRINE_VALUES = ("STRICT", "BALANCED", "AGGRESSIVE")
N_SAMPLES_PRESETS = (300, 500, 1000, 1500)


def _payload_library_normalized() -> dict:
    """Build {category: {subcategory: [payload_params]}} from PAYLOAD_LIBRARY."""
    result: dict = {}
    for p in PAYLOAD_LIBRARY:
        cat = p.get("category", "Other")
        sub = p.get("subcategory", "General")
        if cat not in result:
            result[cat] = {}
        if sub not in result[cat]:
            result[cat][sub] = []
        result[cat][sub].append(p)
    return result


class _FrameClickForwarder(QObject):
    """Event filter: forward frame mouse presses to the associated radio button."""

    def __init__(self, radio: QRadioButton) -> None:
        super().__init__(radio)
        self._radio = radio

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._radio.setChecked(True)
        return False


class ConfigPanel(QFrame):
    """Reusable config panel: title, divider, content, summary."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("configPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame#configPanel { border: 1px solid #1f3a1f; border-radius: 4px; background-color: #0a110a; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(180)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("panelTitle")
        title_lbl.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold; font-size: 13px;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(title_lbl)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        root.addLayout(self.content_layout)
        root.addStretch(1)

        self.summary_label = QLabel("—")
        self.summary_label.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 13px;")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)


class MissionConfigTab(QWidget):
    """
    Mission Config tab: horizontal console-style panels.
    Emits config_committed with full dict to push to config_state.
    """

    config_committed = Signal(dict)
    dirty_changed = Signal(bool)
    threshold_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("missionConfigTab")
        self.setStyleSheet("background-color: #0d140d;")
        self._dirty = False
        self._config_committed = False
        self._mission_mode = "TACTICAL"
        self._simulation_fidelity = "advanced"
        self._doctrine = "BALANCED"
        self._threshold_pct = 75.0
        self._n_samples = 1000
        self._reproducible = False
        self._random_seed = 42
        self._payload_id: str | None = None
        self._mass = 1.0
        self._cd = 0.47
        self._area = 0.01
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ---- Mission Mode + Simulation Fidelity strip (horizontal: two equal blocks) ----
        self._mode_strip = QFrame(self)
        self._mode_strip.setObjectName("missionModeStrip")
        self._mode_strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._mode_strip.setStyleSheet(
            "QFrame#missionModeStrip { border: 1px solid #1a2a1a; border-radius: 4px; background-color: #0a110a; }"
        )
        strip_layout = QHBoxLayout(self._mode_strip)
        strip_layout.setContentsMargins(8, 5, 8, 5)
        strip_layout.setSpacing(12)

        # Left block: Mission Mode
        left_block = QWidget(self._mode_strip)
        left_block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        mode_layout = QVBoxLayout(left_block)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mode_title = QLabel("Mission Mode")
        mode_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold; font-size: 13px;")
        mode_layout.addWidget(mode_title)
        mode_row = QHBoxLayout()
        self._tactical_frame = QFrame(left_block)
        self._tactical_frame.setObjectName("modeOptionFrame")
        self._tactical_frame.setStyleSheet(
            "QFrame#modeOptionFrame { border: 1px solid #2cff05; border-radius: 3px; padding: 3px; background-color: transparent; }"
        )
        self._tactical_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        tactical_inner = QVBoxLayout(self._tactical_frame)
        tactical_inner.setContentsMargins(2, 1, 2, 1)
        self._tactical_radio = QRadioButton("Tactical", self._tactical_frame)
        self._tactical_radio.setChecked(True)
        self._tactical_radio.setStyleSheet(
            "QRadioButton { color: #e8e8e8; border: none; padding-left: 0; min-height: 24px; }"
            "QRadioButton::indicator { width: 0; height: 0; border: none; }"
        )
        self._tactical_radio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tactical_inner.addWidget(self._tactical_radio)
        self._tactical_frame.installEventFilter(_FrameClickForwarder(self._tactical_radio))

        self._humanitarian_frame = QFrame(left_block)
        self._humanitarian_frame.setObjectName("modeOptionFrame")
        self._humanitarian_frame.setStyleSheet(
            "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
        )
        self._humanitarian_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        humanitarian_inner = QVBoxLayout(self._humanitarian_frame)
        humanitarian_inner.setContentsMargins(2, 1, 2, 1)
        self._humanitarian_radio = QRadioButton("Humanitarian", self._humanitarian_frame)
        self._humanitarian_radio.setStyleSheet(
            "QRadioButton { color: #e8e8e8; border: none; padding-left: 0; min-height: 24px; }"
            "QRadioButton::indicator { width: 0; height: 0; border: none; }"
        )
        self._humanitarian_radio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        humanitarian_inner.addWidget(self._humanitarian_radio)
        self._humanitarian_frame.installEventFilter(_FrameClickForwarder(self._humanitarian_radio))

        self._mode_button_group = QButtonGroup(self)
        self._mode_button_group.addButton(self._tactical_radio)
        self._mode_button_group.addButton(self._humanitarian_radio)

        self._tactical_radio.toggled.connect(self._on_mission_mode_changed)
        self._humanitarian_radio.toggled.connect(self._on_mission_mode_changed)
        mode_row.addStretch(1)
        mode_row.addWidget(self._tactical_frame)
        mode_row.addWidget(self._humanitarian_frame)
        mode_row.addStretch(1)
        mode_layout.addLayout(mode_row)
        mode_caption = QLabel(
            "Adjusts presentation style and recommended defaults. Does not modify physics or statistical logic."
        )
        mode_caption.setWordWrap(True)
        mode_caption.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 11px;")
        mode_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        mode_layout.addWidget(mode_caption)
        strip_layout.addWidget(left_block, 1)

        # Right block: Simulation Fidelity
        right_block = QWidget(self._mode_strip)
        right_block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        fidelity_layout = QVBoxLayout(right_block)
        fidelity_layout.setContentsMargins(0, 0, 0, 0)
        fidelity_layout.setSpacing(6)
        fidelity_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        fidelity_title = QLabel("Simulation Fidelity")
        fidelity_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fidelity_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold; font-size: 13px;")
        fidelity_layout.addWidget(fidelity_title)
        fidelity_row = QHBoxLayout()
        self._standard_frame = QFrame(right_block)
        self._standard_frame.setObjectName("modeOptionFrame")
        self._standard_frame.setStyleSheet(
            "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
        )
        self._standard_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        standard_inner = QVBoxLayout(self._standard_frame)
        standard_inner.setContentsMargins(2, 1, 2, 1)
        self._standard_radio = QRadioButton("Standard", self._standard_frame)
        self._standard_radio.setStyleSheet(
            "QRadioButton { color: #e8e8e8; border: none; padding-left: 0; min-height: 24px; }"
            "QRadioButton::indicator { width: 0; height: 0; border: none; }"
        )
        self._standard_radio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        standard_inner.addWidget(self._standard_radio)
        self._standard_frame.installEventFilter(_FrameClickForwarder(self._standard_radio))

        self._advanced_frame = QFrame(right_block)
        self._advanced_frame.setObjectName("modeOptionFrame")
        self._advanced_frame.setStyleSheet(
            "QFrame#modeOptionFrame { border: 1px solid #2cff05; border-radius: 3px; padding: 3px; background-color: transparent; }"
        )
        self._advanced_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        advanced_inner = QVBoxLayout(self._advanced_frame)
        advanced_inner.setContentsMargins(2, 1, 2, 1)
        self._advanced_radio = QRadioButton("Advanced", self._advanced_frame)
        self._advanced_radio.setChecked(True)
        self._advanced_radio.setStyleSheet(
            "QRadioButton { color: #e8e8e8; border: none; padding-left: 0; min-height: 24px; }"
            "QRadioButton::indicator { width: 0; height: 0; border: none; }"
        )
        self._advanced_radio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        advanced_inner.addWidget(self._advanced_radio)
        self._advanced_frame.installEventFilter(_FrameClickForwarder(self._advanced_radio))

        self._fidelity_button_group = QButtonGroup(self)
        self._fidelity_button_group.addButton(self._standard_radio)
        self._fidelity_button_group.addButton(self._advanced_radio)

        self._standard_radio.toggled.connect(self._on_fidelity_changed)
        self._advanced_radio.toggled.connect(self._on_fidelity_changed)
        fidelity_row.addStretch(1)
        fidelity_row.addWidget(self._standard_frame)
        fidelity_row.addWidget(self._advanced_frame)
        fidelity_row.addStretch(1)
        fidelity_layout.addLayout(fidelity_row)
        fidelity_caption = QLabel(
            "Standard: lighter compute. Advanced: full sensitivity and analytical layers."
        )
        fidelity_caption.setWordWrap(True)
        fidelity_caption.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 11px;")
        fidelity_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fidelity_caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        fidelity_layout.addWidget(fidelity_caption)
        strip_layout.addWidget(right_block, 1)

        main_layout.addWidget(self._mode_strip)

        # ---- Threshold strip ----
        self._threshold_strip = QFrame(self)
        self._threshold_strip.setObjectName("thresholdStrip")
        self._threshold_strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._threshold_strip.setStyleSheet(
            "QFrame#thresholdStrip { border: 1px solid #1a2a1a; border-radius: 4px; background-color: #0a110a; }"
        )
        th_layout = QHBoxLayout(self._threshold_strip)
        th_layout.setContentsMargins(10, 6, 10, 6)
        th_layout.setSpacing(8)
        th_label = QLabel("Threshold %", self._threshold_strip)
        th_label.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold; font-size: 13px; min-width: 90px;")
        self._threshold_slider = NoWheelSlider(Qt.Orientation.Horizontal, self._threshold_strip)
        self._threshold_slider.setRange(0, 100)
        self._threshold_slider.setValue(50)  # maps to 75.0
        self._threshold_slider.setStyleSheet(
            "QSlider { border: none; background: transparent; }"
            "QSlider::groove:horizontal { height: 6px; background: #1a2a1a; border-radius: 3px; }"
            "QSlider::sub-page:horizontal { background: #2cff05; border-radius: 3px; }"
            "QSlider::handle:horizontal { width: 12px; margin: -3px 0; background: #2cff05; border-radius: 6px; }"
        )
        self._threshold_spinbox = NoWheelDoubleSpinBox(self._threshold_strip)
        self._threshold_spinbox.setRange(50.0, 100.0)
        self._threshold_spinbox.setSingleStep(0.5)
        self._threshold_spinbox.setDecimals(1)
        self._threshold_spinbox.setValue(75.0)
        self._threshold_spinbox.setFixedWidth(80)
        self._threshold_spinbox.setStyleSheet(INPUT_STYLE)
        th_layout.addWidget(th_label)
        th_layout.addWidget(self._threshold_slider, 1)
        th_layout.addWidget(self._threshold_spinbox)
        self._threshold_slider.valueChanged.connect(self._on_threshold_slider_changed)
        self._threshold_spinbox.valueChanged.connect(self._on_threshold_spinbox_changed)
        main_layout.addWidget(self._threshold_strip)

        # ---- Panels (horizontal) ----
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        panels_widget = QWidget()
        panels_layout = QHBoxLayout(panels_widget)
        panels_layout.setContentsMargins(0, 0, 0, 0)
        panels_layout.setSpacing(10)

        payload_panel = self._build_payload_panel()
        eval_panel = self._build_eval_panel()
        policy_panel = self._build_policy_panel()
        adv_sim_panel = self._build_advanced_simulation_panel()

        panels_layout.addWidget(payload_panel, 1)
        panels_layout.addWidget(eval_panel, 1)
        panels_layout.addWidget(policy_panel, 1)
        panels_layout.addWidget(adv_sim_panel, 1)

        def _equalize_heights() -> None:
            panels_widget.adjustSize()
            h = max(
                payload_panel.sizeHint().height(),
                eval_panel.sizeHint().height(),
                policy_panel.sizeHint().height(),
                adv_sim_panel.sizeHint().height(),
            )
            if h > 0:
                payload_panel.setMinimumHeight(h)
                eval_panel.setMinimumHeight(h)
                policy_panel.setMinimumHeight(h)
                adv_sim_panel.setMinimumHeight(h)

        QTimer.singleShot(50, _equalize_heights)

        scroll.setWidget(panels_widget)
        main_layout.addWidget(scroll, 1)

        main_layout.addSpacing(12)

        # ---- CommitSection ----
        commit_frame = QFrame(self)
        commit_frame.setObjectName("commitFrame")
        commit_frame.setStyleSheet(
            "QFrame#commitFrame { border: 1px solid #1f3a1f; border-radius: 4px; background-color: #0a110a; }"
        )
        commit_frame_layout = QVBoxLayout(commit_frame)
        commit_frame_layout.setContentsMargins(8, 6, 8, 6)
        commit_frame_layout.setSpacing(4)
        self._commit_btn = QPushButton("Commit Configuration", self)
        self._commit_btn.setMinimumHeight(32)
        self._commit_btn.setStyleSheet(
            f"QPushButton {{ background-color: #1a2a1a; color: {PRIMARY_COLOR}; border: 1px solid #2a3a2a; border-radius: 4px; font-size: 15px; }}"
            "QPushButton:hover { background-color: #2a3a2a; }"
            "QPushButton:pressed { background-color: #0d140d; }"
        )
        self._commit_btn.clicked.connect(self._on_commit_clicked)
        commit_frame_layout.addWidget(self._commit_btn)
        main_layout.addWidget(commit_frame)

        self._update_panel_summaries()
        self._update_mode_strip_border()
        self._update_fidelity_strip_border()

    def _build_payload_panel(self) -> ConfigPanel:
        panel = ConfigPanel("Payload & Release Profile", self)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._category_combo = QComboBox(panel)
        self._category_combo.addItem("— Select category —", "")
        for c in CATEGORIES:
            self._category_combo.addItem(c, c)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        self._category_combo.setStyleSheet(INPUT_STYLE)
        lbl_cat = QLabel("Category")
        lbl_cat.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_cat, self._category_combo)

        self._payload_combo = QComboBox(panel)
        self._payload_combo.addItem("— Select payload —", "")
        self._payload_combo.currentIndexChanged.connect(self._on_payload_changed)
        self._payload_combo.setStyleSheet(INPUT_STYLE)
        lbl_pay = QLabel("Payload")
        lbl_pay.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_pay, self._payload_combo)

        self._mass_spin = NoWheelDoubleSpinBox(panel)
        self._mass_spin.setRange(0.1, 1000.0)
        self._mass_spin.setSingleStep(0.1)
        self._mass_spin.setDecimals(3)
        self._mass_spin.setValue(1.0)
        self._mass_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._mass_spin.setStyleSheet(INPUT_STYLE)
        lbl_m = QLabel("Mass (kg)")
        lbl_m.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_m, self._mass_spin)

        self._cd_spin = NoWheelDoubleSpinBox(panel)
        self._cd_spin.setRange(0.01, 10.0)
        self._cd_spin.setSingleStep(0.01)
        self._cd_spin.setDecimals(3)
        self._cd_spin.setValue(0.47)
        self._cd_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._cd_spin.setStyleSheet(INPUT_STYLE)
        lbl_cd = QLabel("Drag Coef.")
        lbl_cd.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_cd, self._cd_spin)

        self._area_spin = NoWheelDoubleSpinBox(panel)
        self._area_spin.setRange(0.001, 10.0)
        self._area_spin.setSingleStep(0.001)
        self._area_spin.setDecimals(4)
        self._area_spin.setValue(0.01)
        self._area_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._area_spin.setStyleSheet(INPUT_STYLE)
        lbl_a = QLabel("Area (m²)")
        lbl_a.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_a, self._area_spin)

        panel.content_layout.addLayout(form)
        self._payload_panel = panel
        return panel

    def _build_eval_panel(self) -> ConfigPanel:
        panel = ConfigPanel("Evaluation Depth", self)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._n_samples_spin = NoWheelSpinBox(panel)
        self._n_samples_spin.setRange(30, 10000)
        self._n_samples_spin.setSingleStep(50)
        self._n_samples_spin.setValue(1000)
        self._n_samples_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._n_samples_spin.setStyleSheet(INPUT_STYLE)
        lbl_n = QLabel("Samples")
        lbl_n.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_n, self._n_samples_spin)

        preset_row = QHBoxLayout()
        for n in N_SAMPLES_PRESETS:
            btn = QPushButton(str(n), panel)
            btn.setFixedWidth(45)
            btn.setStyleSheet(f"{BTN_STYLE}")
            btn.clicked.connect(lambda checked, v=n: self._set_n_samples(v))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        lbl_presets = QLabel("Presets")
        lbl_presets.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_presets, preset_row)

        self._reproducible_check = QCheckBox("Reproducible", panel)
        self._reproducible_check.setChecked(False)
        self._reproducible_check.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self._reproducible_check.stateChanged.connect(self._on_reproducible_changed)
        form.addRow(self._reproducible_check)

        self._seed_row_label = QLabel("Seed")
        self._seed_row_label.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self._seed_spin = NoWheelSpinBox(panel)
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(42)
        self._seed_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._seed_spin.setStyleSheet(INPUT_STYLE)
        form.addRow(self._seed_row_label, self._seed_spin)
        self._seed_spin.setVisible(False)
        self._seed_row_label.setVisible(False)

        panel.content_layout.addLayout(form)
        self._eval_panel = panel
        return panel

    def _build_policy_panel(self) -> ConfigPanel:
        panel = ConfigPanel("Decision Policy", self)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._doctrine_combo = QComboBox(panel)
        for d in DOCTRINE_VALUES:
            self._doctrine_combo.addItem(d, d)
        self._doctrine_combo.setCurrentIndex(1)
        self._doctrine_combo.currentIndexChanged.connect(self._on_doctrine_changed)
        self._doctrine_combo.setStyleSheet(INPUT_STYLE)
        lbl_d = QLabel("Doctrine")
        lbl_d.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_d, self._doctrine_combo)

        self._doctrine_desc_label = QLabel(DOCTRINE_DESCRIPTIONS.get("BALANCED", ""))
        self._doctrine_desc_label.setWordWrap(True)
        self._doctrine_desc_label.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 11px;")
        form.addRow(self._doctrine_desc_label)

        panel.content_layout.addLayout(form)
        self._policy_panel = panel
        return panel

    def _build_advanced_simulation_panel(self) -> ConfigPanel:
        """Advanced Simulation section: 3D spatial overrides. Disabled in LIVE mode."""
        panel = ConfigPanel("Advanced Simulation", self)
        caption = QLabel("Simulation Override — Ignored in Live Mode")
        caption.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 10px;")
        caption.setWordWrap(True)
        panel.content_layout.addWidget(caption)

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._uav_altitude_spin = NoWheelDoubleSpinBox(panel)
        self._uav_altitude_spin.setRange(10.0, 100_000.0)
        self._uav_altitude_spin.setSingleStep(10.0)
        self._uav_altitude_spin.setDecimals(2)
        self._uav_altitude_spin.setValue(100.0)
        self._uav_altitude_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._uav_altitude_spin.setStyleSheet(INPUT_STYLE)
        lbl_alt = QLabel("UAV Altitude (m)")
        lbl_alt.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_alt, self._uav_altitude_spin)

        self._target_x_spin = NoWheelDoubleSpinBox(panel)
        self._target_x_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._target_x_spin.setSingleStep(1.0)
        self._target_x_spin.setDecimals(2)
        self._target_x_spin.setValue(72.0)
        self._target_x_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._target_x_spin.setStyleSheet(INPUT_STYLE)
        lbl_tx = QLabel("Target X (m)")
        lbl_tx.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_tx, self._target_x_spin)

        self._target_y_spin = NoWheelDoubleSpinBox(panel)
        self._target_y_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._target_y_spin.setSingleStep(1.0)
        self._target_y_spin.setDecimals(2)
        self._target_y_spin.setValue(0.0)
        self._target_y_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._target_y_spin.setStyleSheet(INPUT_STYLE)
        lbl_ty = QLabel("Target Y (m)")
        lbl_ty.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_ty, self._target_y_spin)

        self._target_elevation_spin = NoWheelDoubleSpinBox(panel)
        self._target_elevation_spin.setRange(-10_000.0, 10_000.0)
        self._target_elevation_spin.setSingleStep(1.0)
        self._target_elevation_spin.setDecimals(1)
        self._target_elevation_spin.setValue(0.0)
        self._target_elevation_spin.valueChanged.connect(lambda _: (self._set_dirty(True), self._update_panel_summaries()))
        self._target_elevation_spin.setStyleSheet(INPUT_STYLE)
        lbl_te = QLabel("Target Elevation (m)")
        lbl_te.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_te, self._target_elevation_spin)

        panel.content_layout.addLayout(form)
        self._adv_sim_panel = panel
        self._adv_sim_widgets = [
            self._uav_altitude_spin,
            self._target_x_spin,
            self._target_y_spin,
            self._target_elevation_spin,
        ]
        return panel

    def _update_panel_summaries(self) -> None:
        """Update dynamic summary labels on all panels."""
        if hasattr(self, "_payload_panel"):
            pay_name = self._payload_combo.currentText() or "—"
            self._payload_panel.summary_label.setText(
                f"{pay_name} | m={self._mass_spin.value():.2f} Cd={self._cd_spin.value():.2f}"
            )
        if hasattr(self, "_eval_panel"):
            rep = "Fixed" if self._reproducible else "Auto"
            self._eval_panel.summary_label.setText(
                f"n={self._n_samples_spin.value()} | Seed: {rep}"
            )
        if hasattr(self, "_policy_panel"):
            d = self._doctrine_combo.currentData() or self._doctrine
            desc = DOCTRINE_DESCRIPTIONS.get(str(d), "")
            self._policy_panel.summary_label.setText(desc[:60] + "…" if len(desc) > 60 else desc)
        if hasattr(self, "_adv_sim_panel"):
            self._adv_sim_panel.summary_label.setText(
                f"H={self._uav_altitude_spin.value():.0f}m | T=({self._target_x_spin.value():.0f}, {self._target_y_spin.value():.0f}, {self._target_elevation_spin.value():.0f})m"
            )

    def apply_system_mode(self, mode: str) -> None:
        """Enable/disable Advanced Simulation section. Disabled in LIVE (telemetry-driven)."""
        mode = str(mode or "SNAPSHOT").strip().upper()
        enabled = mode != "LIVE"
        if hasattr(self, "_adv_sim_widgets"):
            for w in self._adv_sim_widgets:
                w.setEnabled(enabled)

    def _update_mode_strip_border(self) -> None:
        bc = "#2cff05" if self._mission_mode == "TACTICAL" else "#38e84a"
        self._mode_strip.setStyleSheet(
            f"QFrame#missionModeStrip {{ border: 1px solid {bc}; border-radius: 4px; background-color: #0a110a; }}"
        )
        if self._mission_mode == "TACTICAL":
            self._tactical_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid #2cff05; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
            self._humanitarian_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
        else:
            self._tactical_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
            self._humanitarian_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid #38e84a; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )

    def _update_fidelity_strip_border(self) -> None:
        """Update Standard/Advanced frame borders to show selected fidelity."""
        if self._simulation_fidelity == "standard":
            self._standard_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid #2cff05; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
            self._advanced_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
        else:
            self._standard_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid transparent; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )
            self._advanced_frame.setStyleSheet(
                "QFrame#modeOptionFrame { border: 1px solid #2cff05; border-radius: 3px; padding: 3px; background-color: transparent; }"
            )

    def _on_fidelity_changed(self) -> None:
        """Update simulation_fidelity from Standard/Advanced toggle. Does not push config or run simulation."""
        if self._standard_radio.isChecked():
            new_fidelity = "standard"
        else:
            new_fidelity = "advanced"
        if new_fidelity == self._simulation_fidelity:
            return
        self._simulation_fidelity = new_fidelity
        self._update_fidelity_strip_border()
        self._set_dirty(True)
        self._update_panel_summaries()

    def _set_n_samples(self, n: int) -> None:
        self._n_samples_spin.setValue(n)
        self._set_dirty(True)
        self._update_panel_summaries()

    def _on_reproducible_changed(self, state: int) -> None:
        self._reproducible = state == 2
        self._seed_spin.setVisible(self._reproducible)
        self._seed_row_label.setVisible(self._reproducible)
        self._set_dirty(True)
        self._update_panel_summaries()

    def _on_doctrine_changed(self, _idx: int) -> None:
        d = self._doctrine_combo.currentData()
        if d:
            self._doctrine = str(d)
            self._update_doctrine_display()
            self._set_dirty(True)
            self._update_panel_summaries()

    def _update_doctrine_display(self) -> None:
        desc = DOCTRINE_DESCRIPTIONS.get(self._doctrine, "")
        self._doctrine_desc_label.setText(desc)
        self._doctrine_combo.blockSignals(True)
        for i in range(self._doctrine_combo.count()):
            data = self._doctrine_combo.itemData(i)
            if data:
                d = str(data)
                if self._mission_mode == "HUMANITARIAN":
                    display = DOCTRINE_DISPLAY_LABELS_HUMANITARIAN.get(d, d)
                    self._doctrine_combo.setItemText(i, f"{d} — {display}")
                else:
                    self._doctrine_combo.setItemText(i, d)
        self._doctrine_combo.blockSignals(False)

    def _on_category_changed(self, _idx: int) -> None:
        cat = self._category_combo.currentData()
        self._payload_combo.clear()
        self._payload_combo.addItem("— Select payload —", "")
        if cat:
            lib = _payload_library_normalized()
            if cat in lib:
                for sub, payloads in lib[cat].items():
                    for p in payloads:
                        name = p.get("name", p.get("id", "?"))
                        pid = p.get("id", "")
                        self._payload_combo.addItem(name, pid)
        self._set_dirty(True)
        self._update_panel_summaries()

    def _on_payload_changed(self, _idx: int) -> None:
        pid = self._payload_combo.currentData()
        if pid:
            try:
                mass, cd, area = get_default_physics_for_payload(pid)
                self._mass_spin.setValue(mass)
                self._cd_spin.setValue(cd)
                self._area_spin.setValue(area)
                self._payload_id = str(pid)
            except Exception:
                self._payload_id = None
        else:
            self._payload_id = None
        self._set_dirty(True)
        self._update_panel_summaries()

    @Slot(int)
    def _on_threshold_slider_changed(self, value: int) -> None:
        val = 50.0 + value * 0.5
        self._threshold_spinbox.blockSignals(True)
        self._threshold_spinbox.setValue(val)
        self._threshold_spinbox.blockSignals(False)
        self._threshold_pct = val
        self.threshold_changed.emit(val)

    @Slot(float)
    def _on_threshold_spinbox_changed(self, value: float) -> None:
        self._threshold_slider.blockSignals(True)
        self._threshold_slider.setValue(int((value - 50.0) / 0.5))
        self._threshold_slider.blockSignals(False)
        self._threshold_pct = value
        self.threshold_changed.emit(value)

    def _on_mission_mode_changed(self) -> None:
        if self._tactical_radio.isChecked():
            new_mode = "TACTICAL"
        else:
            new_mode = "HUMANITARIAN"
        if new_mode == self._mission_mode:
            return
        prev = self._mission_mode
        self._mission_mode = new_mode
        self._update_doctrine_display()
        self._update_mode_strip_border()

        if not self._config_committed:
            if new_mode == "TACTICAL":
                self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("BALANCED"))
                self._n_samples_spin.setValue(1000)
            else:
                self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("STRICT"))
                self._n_samples_spin.setValue(1500)
            self._update_doctrine_display()
        else:
            reply = QMessageBox.question(
                self,
                "Apply recommended defaults?",
                f"Switch to {new_mode} mode? Apply recommended defaults?\n"
                f"TACTICAL: Balanced, 1000 samples\n"
                f"HUMANITARIAN: Strict, 1500 samples",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if new_mode == "TACTICAL":
                    self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("BALANCED"))
                    self._n_samples_spin.setValue(1000)
                else:
                    self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("STRICT"))
                    self._n_samples_spin.setValue(1500)
                self._update_doctrine_display()
            else:
                self._mission_mode = prev
                if prev == "TACTICAL":
                    self._tactical_radio.setChecked(True)
                else:
                    self._humanitarian_radio.setChecked(True)
                self._update_mode_strip_border()
                return
        self._set_dirty(True)
        self._update_panel_summaries()

    def _set_dirty(self, value: bool) -> None:
        if self._dirty == value:
            return
        self._dirty = value
        self.dirty_changed.emit(value)
        self._update_commit_button_style()

    def _update_commit_button_style(self) -> None:
        if self._dirty:
            self._commit_btn.setText("Commit Configuration (Uncommitted Changes)")
            self._commit_btn.setStyleSheet(
                "QPushButton { background-color: #3d2a00; color: #ffcc00; border: 2px solid #ffaa00; border-radius: 4px; font-size: 15px; }"
                "QPushButton:hover { background-color: #4d3a10; }"
                "QPushButton:pressed { background-color: #2d1a00; }"
            )
        else:
            self._commit_btn.setText("Commit Configuration")
            self._commit_btn.setStyleSheet(
                f"QPushButton {{ background-color: #1a2a1a; color: {PRIMARY_COLOR}; border: 1px solid #2a3a2a; border-radius: 4px; font-size: 15px; }}"
                "QPushButton:hover { background-color: #2a3a2a; }"
                "QPushButton:pressed { background-color: #0d140d; }"
            )

    def _on_commit_clicked(self) -> None:
        cfg = self.get_config()
        self.config_committed.emit(cfg)
        self._dirty = False
        self._config_committed = True
        self.dirty_changed.emit(False)
        self._update_commit_button_style()
        self._update_panel_summaries()

    def get_config(self) -> dict:
        """Return full config dict for config_state push."""
        seed = self._seed_spin.value() if self._reproducible else random.randint(0, 2_147_483_647)
        fidelity = (self._simulation_fidelity or "advanced").strip().lower()
        if fidelity not in FIDELITY_VALUES:
            fidelity = "advanced"
        return {
            "mission_mode": self._mission_mode,
            "doctrine_mode": self._doctrine,
            "simulation_fidelity": fidelity,
            "payload_id": self._payload_id,
            "threshold_pct": self._threshold_spinbox.value(),
            "n_samples": self._n_samples_spin.value(),
            "random_seed": seed,
            "reproducible": self._reproducible,
            "mass": self._mass_spin.value(),
            "cd": self._cd_spin.value(),
            "area": self._area_spin.value(),
            "uav_altitude": float(self._uav_altitude_spin.value()),
            "target_x": float(self._target_x_spin.value()),
            "target_y": float(self._target_y_spin.value()),
            "target_elevation": float(self._target_elevation_spin.value()),
        }

    def init_from_config(self, cfg: dict) -> None:
        """Initialize form from config. Does not set dirty."""
        th = float(cfg.get("threshold_pct", 75.0))
        self._threshold_slider.blockSignals(True)
        self._threshold_spinbox.blockSignals(True)
        self._threshold_spinbox.setValue(th)
        self._threshold_slider.setValue(int((th - 50.0) / 0.5))
        self._threshold_pct = th
        self._threshold_slider.blockSignals(False)
        self._threshold_spinbox.blockSignals(False)
        fidelity = str(cfg.get("simulation_fidelity", "advanced")).strip().lower()
        if fidelity not in FIDELITY_VALUES:
            fidelity = "advanced"
        self._simulation_fidelity = fidelity
        if hasattr(self, "_standard_radio") and hasattr(self, "_advanced_radio"):
            self._standard_radio.blockSignals(True)
            self._advanced_radio.blockSignals(True)
            self._standard_radio.setChecked(fidelity == "standard")
            self._advanced_radio.setChecked(fidelity == "advanced")
            self._standard_radio.blockSignals(False)
            self._advanced_radio.blockSignals(False)
            self._update_fidelity_strip_border()
        self._mass_spin.blockSignals(True)
        self._cd_spin.blockSignals(True)
        self._area_spin.blockSignals(True)
        self._n_samples_spin.blockSignals(True)
        self._seed_spin.blockSignals(True)
        self._mass_spin.setValue(float(cfg.get("mass", 1.0)))
        self._cd_spin.setValue(float(cfg.get("cd", 0.47)))
        self._area_spin.setValue(float(cfg.get("area", 0.01)))
        self._n_samples_spin.setValue(int(cfg.get("n_samples", 1000)))
        self._seed_spin.setValue(int(cfg.get("random_seed", 42)))
        if hasattr(self, "_uav_altitude_spin"):
            self._uav_altitude_spin.blockSignals(True)
            self._target_x_spin.blockSignals(True)
            self._target_y_spin.blockSignals(True)
            self._target_elevation_spin.blockSignals(True)
            self._uav_altitude_spin.setValue(float(cfg.get("uav_altitude", 100.0)))
            self._target_x_spin.setValue(float(cfg.get("target_x", 72.0)))
            self._target_y_spin.setValue(float(cfg.get("target_y", 0.0)))
            tp = cfg.get("target_pos")
            te = cfg.get("target_elevation")
            if te is not None:
                te_val = float(te)
            elif isinstance(tp, (list, tuple)) and len(tp) >= 3:
                te_val = float(tp[2])
            else:
                te_val = 0.0
            self._target_elevation_spin.setValue(te_val)
            self._uav_altitude_spin.blockSignals(False)
            self._target_x_spin.blockSignals(False)
            self._target_y_spin.blockSignals(False)
            self._target_elevation_spin.blockSignals(False)
        self._mass_spin.blockSignals(False)
        self._cd_spin.blockSignals(False)
        self._area_spin.blockSignals(False)
        self._n_samples_spin.blockSignals(False)
        self._seed_spin.blockSignals(False)
        self._update_panel_summaries()

    def load_from_snapshot(self, snapshot: dict) -> None:
        """Update display from snapshot. Does not set dirty."""
        fidelity = str(snapshot.get("simulation_fidelity", "advanced")).strip().lower()
        if fidelity not in FIDELITY_VALUES:
            fidelity = "advanced"
        self._simulation_fidelity = fidelity
        if hasattr(self, "_standard_radio") and hasattr(self, "_advanced_radio"):
            self._standard_radio.blockSignals(True)
            self._advanced_radio.blockSignals(True)
            self._standard_radio.setChecked(fidelity == "standard")
            self._advanced_radio.setChecked(fidelity == "advanced")
            self._standard_radio.blockSignals(False)
            self._advanced_radio.blockSignals(False)
            self._update_fidelity_strip_border()
        mode = str(snapshot.get("mission_mode", "TACTICAL")).strip().upper()
        if mode in MISSION_MODES:
            self._mission_mode = mode
            self._tactical_radio.blockSignals(True)
            self._humanitarian_radio.blockSignals(True)
            self._tactical_radio.setChecked(mode == "TACTICAL")
            self._humanitarian_radio.setChecked(mode == "HUMANITARIAN")
            self._tactical_radio.blockSignals(False)
            self._humanitarian_radio.blockSignals(False)
            self._update_mode_strip_border()
        th = snapshot.get("threshold_pct")
        if th is not None:
            try:
                tv = float(th)
                if 50.0 <= tv <= 100.0:
                    self._threshold_slider.blockSignals(True)
                    self._threshold_spinbox.blockSignals(True)
                    self._threshold_spinbox.setValue(tv)
                    self._threshold_slider.setValue(int((tv - 50.0) / 0.5))
                    self._threshold_pct = tv
                    self._threshold_slider.blockSignals(False)
                    self._threshold_spinbox.blockSignals(False)
            except (TypeError, ValueError):
                pass
        doctrine = str(snapshot.get("doctrine_mode", "BALANCED")).strip().upper()
        if doctrine in DOCTRINE_VALUES:
            idx = self._doctrine_combo.findData(doctrine)
            if idx >= 0:
                self._doctrine_combo.blockSignals(True)
                self._doctrine_combo.setCurrentIndex(idx)
                self._doctrine_combo.blockSignals(False)
                self._doctrine = doctrine
        self._update_doctrine_display()
        self._update_panel_summaries()
