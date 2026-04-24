"""
Mission Config tab widget: horizontal console-style panels.
Mission Mode strip, Payload/Target/Policy panels, Commit.
Reads from config snapshot for display; commits push to config_state.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from widgets import NoWheelDoubleSpinBox, NoWheelSlider

from product.ui.tabs.payload_library import PAYLOAD_LIBRARY, CATEGORIES
from src.decision_doctrine import DOCTRINE_DESCRIPTIONS


# Shape aerodynamic constants — mirrored from compute_CdA internals for display
SHAPE_CD = {
    "sphere": 0.47, "cylinder": 0.90, "box": 1.15,
    "capsule": 0.50, "blunt_cone": 0.70,
}
SHAPE_UNC = {
    "sphere": 0.05, "cylinder": 0.15, "box": 0.20,
    "capsule": 0.10, "blunt_cone": 0.15,
}

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
DOCTRINE_VALUES = ("STRICT", "BALANCED", "AGGRESSIVE")


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        self._doctrine = "BALANCED"
        self._threshold_pct = 75.0
        self._payload_id: str | None = None
        self._mass = 1.0
        self._shape = "box"
        self._dims = [0.2, 0.2]
        self._CdA = 0.0
        self._cd_uncertainty = 0.20
        self._build_ui()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ---- Strips container: mode + threshold, with side/top margins ----
        strips_container = QWidget(self)
        strips_layout = QVBoxLayout(strips_container)
        strips_layout.setContentsMargins(10, 10, 10, 0)
        strips_layout.setSpacing(8)

        # ---- Mission Mode strip ----
        self._mode_strip = QFrame(self)
        self._mode_strip.setObjectName("missionModeStrip")
        self._mode_strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._mode_strip.setStyleSheet(
            "QFrame#missionModeStrip { border: 1px solid #1a2a1a; border-radius: 4px; background-color: #0a110a; }"
        )
        strip_layout = QHBoxLayout(self._mode_strip)
        strip_layout.setContentsMargins(8, 5, 8, 5)
        strip_layout.setSpacing(12)

        mode_block = QWidget(self._mode_strip)
        mode_block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        mode_layout = QVBoxLayout(mode_block)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mode_title = QLabel("Mission Mode")
        mode_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-weight: bold; font-size: 13px;")
        mode_layout.addWidget(mode_title)
        mode_row = QHBoxLayout()

        self._tactical_frame = QFrame(mode_block)
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

        self._humanitarian_frame = QFrame(mode_block)
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
            "Tactical: CEP-optimized, strict P(hit) on point target. Humanitarian: Zone delivery, conservative threshold."
        )
        mode_caption.setWordWrap(True)
        mode_caption.setStyleSheet(f"color: {SECONDARY_COLOR}; font-size: 11px;")
        mode_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        mode_layout.addWidget(mode_caption)
        strip_layout.addWidget(mode_block, 1)

        strips_layout.addWidget(self._mode_strip)

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
        strips_layout.addWidget(self._threshold_strip)
        outer_layout.addWidget(strips_container)

        # ---- Panels (horizontal, no scroll — fills remaining space) ----
        panels_widget = QWidget(self)
        panels_layout = QHBoxLayout(panels_widget)
        panels_layout.setContentsMargins(10, 0, 10, 0)
        panels_layout.setSpacing(8)

        payload_panel = self._build_payload_panel()
        target_panel = self._build_target_panel()
        policy_panel = self._build_policy_panel()

        panels_layout.addWidget(payload_panel, 1)
        panels_layout.addWidget(target_panel, 1)
        panels_layout.addWidget(policy_panel, 1)

        outer_layout.addWidget(panels_widget, 1)

        # ---- Commit Section — fixed outside scroll, always visible ----
        _commit_wrapper = QWidget(self)
        _commit_wrapper_layout = QVBoxLayout(_commit_wrapper)
        _commit_wrapper_layout.setContentsMargins(10, 6, 10, 10)
        _commit_wrapper_layout.setSpacing(0)
        commit_frame = QFrame(_commit_wrapper)
        commit_frame.setObjectName("commitFrame")
        commit_frame.setStyleSheet(
            "QFrame#commitFrame { border: 1px solid #1f3a1f; border-radius: 4px; background-color: #0a110a; }"
        )
        commit_frame_layout = QVBoxLayout(commit_frame)
        commit_frame_layout.setContentsMargins(8, 6, 8, 6)
        commit_frame_layout.setSpacing(4)
        self._commit_btn = QPushButton("Commit Configuration", self)
        self._commit_btn.setMinimumHeight(32)
        self._commit_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._commit_btn.setStyleSheet(
            f"QPushButton {{ background-color: #1a2a1a; color: {PRIMARY_COLOR}; border: 1px solid #2a3a2a; border-radius: 4px; font-size: 15px; }}"
            "QPushButton:hover { background-color: #2a3a2a; }"
            "QPushButton:pressed { background-color: #0d140d; }"
        )
        self._commit_btn.clicked.connect(self._on_commit_clicked)
        commit_frame_layout.addWidget(self._commit_btn)
        _commit_wrapper_layout.addWidget(commit_frame)
        outer_layout.addWidget(_commit_wrapper)

        self._update_panel_summaries()
        self._update_mode_strip_border()

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
        self._mass_spin.setStyleSheet(INPUT_STYLE)
        lbl_m = QLabel("Mass (kg)")
        lbl_m.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_m, self._mass_spin)

        self._shape_combo = QComboBox(panel)
        for s in ("sphere", "cylinder", "box", "capsule", "blunt_cone"):
            self._shape_combo.addItem(s, s)
        self._shape_combo.setCurrentText("box")
        self._shape_combo.setStyleSheet(INPUT_STYLE)
        lbl_sh = QLabel("Shape")
        lbl_sh.setStyleSheet(f"color: {PRIMARY_COLOR};")
        form.addRow(lbl_sh, self._shape_combo)

        self._dim1_label = QLabel("Length (m)")
        self._dim1_label.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self._dim1_spin = NoWheelDoubleSpinBox(panel)
        self._dim1_spin.setRange(0.001, 10.0)
        self._dim1_spin.setSingleStep(0.01)
        self._dim1_spin.setDecimals(3)
        self._dim1_spin.setValue(0.2)
        self._dim1_spin.setStyleSheet(INPUT_STYLE)
        form.addRow(self._dim1_label, self._dim1_spin)

        self._dim2_label = QLabel("Width (m)")
        self._dim2_label.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self._dim2_spin = NoWheelDoubleSpinBox(panel)
        self._dim2_spin.setRange(0.001, 10.0)
        self._dim2_spin.setSingleStep(0.01)
        self._dim2_spin.setDecimals(3)
        self._dim2_spin.setValue(0.2)
        self._dim2_spin.setStyleSheet(INPUT_STYLE)
        form.addRow(self._dim2_label, self._dim2_spin)

        self._cd_display = QLabel("Cd: 1.15  (box)")
        self._cd_display.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 12px;")
        form.addRow(self._cd_display)

        self._cda_display = QLabel("CdA: 0.0460 m²")
        self._cda_display.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 12px;")
        form.addRow(self._cda_display)

        self._beta_display = QLabel("β: 21.7 kg/m²")
        self._beta_display.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 12px;")
        form.addRow(self._beta_display)

        panel.content_layout.addLayout(form)
        self._payload_panel = panel

        # Connect signals — initial values already set above, so no spurious fire
        self._shape_combo.currentTextChanged.connect(self._recompute_payload_physics)
        self._dim1_spin.valueChanged.connect(self._recompute_payload_physics)
        self._dim2_spin.valueChanged.connect(self._recompute_payload_physics)
        self._mass_spin.valueChanged.connect(self._recompute_payload_physics)

        # Populate display labels (commit_btn not yet built — guard inside method)
        self._recompute_payload_physics()

        return panel

    def _build_target_panel(self) -> ConfigPanel:
        panel = ConfigPanel("Target Configuration", self)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._target_status_label = QLabel("⬤ Target: Not Set")
        self._target_status_label.setStyleSheet("color: #ff4444; font-size: 12px;")
        form.addRow(self._target_status_label)

        lbl_tr = QLabel("Target Radius (m)")
        lbl_tr.setStyleSheet(f"color: {PRIMARY_COLOR};")
        self._target_radius_spin = NoWheelDoubleSpinBox(panel)
        self._target_radius_spin.setRange(1.0, 500.0)
        self._target_radius_spin.setSingleStep(1.0)
        self._target_radius_spin.setDecimals(1)
        self._target_radius_spin.setValue(15.0)
        self._target_radius_spin.setStyleSheet(INPUT_STYLE)
        self._target_radius_spin.valueChanged.connect(
            lambda _: (self._set_dirty(True), self._update_panel_summaries())
        )
        form.addRow(lbl_tr, self._target_radius_spin)

        panel.content_layout.addLayout(form)
        self._target_panel = panel
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

    def _recompute_payload_physics(self) -> None:
        """Called when shape, dims, or mass changes."""
        from product.ui.tabs.payload_library import compute_CdA
        shape = self._shape_combo.currentText()
        d1 = self._dim1_spin.value()
        d2 = self._dim2_spin.value() if shape == "box" else d1
        dims = [d1, d2] if shape == "box" else [d1]
        area, CdA = compute_CdA(shape, dims)
        mass = self._mass_spin.value()
        beta = mass / max(CdA, 1e-6)

        cd = SHAPE_CD.get(shape, 1.0)

        self._shape = shape
        self._dims = dims
        self._CdA = CdA
        self._cd_uncertainty = SHAPE_UNC.get(shape, 0.20)

        self._cd_display.setText(f"Cd: {cd:.2f}  ({shape})")
        self._cda_display.setText(f"CdA: {CdA:.4f} m²")
        self._beta_display.setText(f"β: {beta:.1f} kg/m²")

        # Update dim1 label based on shape semantics
        if shape in ("sphere", "cylinder", "blunt_cone"):
            self._dim1_label.setText("Diameter (m)")
        else:
            self._dim1_label.setText("Length (m)")

        # dim2 is only meaningful for box
        self._dim2_label.setVisible(shape == "box")
        self._dim2_spin.setVisible(shape == "box")

        # Guard: commit_btn not yet created during _build_payload_panel init call
        if hasattr(self, "_commit_btn"):
            self._set_dirty(True)
        if hasattr(self, "_payload_panel"):
            self._update_panel_summaries()

    def _update_panel_summaries(self) -> None:
        """Update dynamic summary labels on all panels."""
        if hasattr(self, "_payload_panel"):
            pay_name = self._payload_combo.currentText() if hasattr(self, "_payload_combo") else "—"
            self._payload_panel.summary_label.setText(
                f"{pay_name} | m={self._mass_spin.value():.2f} CdA={self._CdA:.4f}"
            )
        if hasattr(self, "_target_panel") and hasattr(self, "_target_radius_spin"):
            self._target_panel.summary_label.setText(
                f"r={self._target_radius_spin.value():.1f} m"
            )
        if hasattr(self, "_policy_panel"):
            d = self._doctrine_combo.currentData() or self._doctrine
            desc = DOCTRINE_DESCRIPTIONS.get(str(d), "")
            self._policy_panel.summary_label.setText(desc[:60] + "…" if len(desc) > 60 else desc)

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
            entry = next((p for p in PAYLOAD_LIBRARY if p.get("id") == pid), None)
            if entry is not None:
                mass = float(entry.get("mass_kg", entry.get("mass", 1.0)))
                shape = str(entry.get("shape", "box"))
                dims = list(entry.get("dims", [0.2, 0.2]))

                self._shape_combo.blockSignals(True)
                self._mass_spin.blockSignals(True)
                self._dim1_spin.blockSignals(True)
                self._dim2_spin.blockSignals(True)

                idx = self._shape_combo.findText(shape)
                if idx >= 0:
                    self._shape_combo.setCurrentIndex(idx)
                self._mass_spin.setValue(mass)
                if len(dims) >= 1:
                    self._dim1_spin.setValue(float(dims[0]))
                if len(dims) >= 2:
                    self._dim2_spin.setValue(float(dims[1]))
                else:
                    self._dim2_spin.setValue(float(dims[0]))

                self._shape_combo.blockSignals(False)
                self._mass_spin.blockSignals(False)
                self._dim1_spin.blockSignals(False)
                self._dim2_spin.blockSignals(False)

                self._payload_id = str(pid)
            else:
                self._payload_id = None
        else:
            self._payload_id = None
        self._recompute_payload_physics()

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
                if hasattr(self, "_target_radius_spin"):
                    self._target_radius_spin.setValue(15.0)
                self._threshold_spinbox.setValue(75.0)
            else:
                self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("STRICT"))
                if hasattr(self, "_target_radius_spin"):
                    self._target_radius_spin.setValue(100.0)
                self._threshold_spinbox.setValue(55.0)
            self._update_doctrine_display()
        else:
            if new_mode == "TACTICAL":
                defaults_desc = "Balanced doctrine, 15 m radius, 75% threshold"
            else:
                defaults_desc = "Strict doctrine, 100 m radius, 55% threshold"
            reply = QMessageBox.question(
                self,
                "Apply recommended defaults?",
                f"Switch to {new_mode} mode? Apply recommended defaults?\n{defaults_desc}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if new_mode == "TACTICAL":
                    self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("BALANCED"))
                    if hasattr(self, "_target_radius_spin"):
                        self._target_radius_spin.setValue(15.0)
                    self._threshold_spinbox.setValue(75.0)
                else:
                    self._doctrine_combo.setCurrentIndex(DOCTRINE_VALUES.index("STRICT"))
                    if hasattr(self, "_target_radius_spin"):
                        self._target_radius_spin.setValue(100.0)
                    self._threshold_spinbox.setValue(55.0)
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
        cd = SHAPE_CD.get(self._shape, 1.0)
        area = self._CdA / max(cd, 1e-6)
        return {
            "mission_mode": self._mission_mode,
            "doctrine_mode": self._doctrine,
            "payload_id": self._payload_id,
            "threshold_pct": self._threshold_spinbox.value(),
            "mass": self._mass_spin.value(),
            "shape": self._shape,
            "dims": list(self._dims),
            "cd": cd,
            "area": round(area, 6),
            "CdA": self._CdA,
            "cd_uncertainty": self._cd_uncertainty,
            "target_radius": self._target_radius_spin.value(),
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

        self._mass_spin.blockSignals(True)
        self._mass_spin.setValue(float(cfg.get("mass", 1.0)))
        self._mass_spin.blockSignals(False)

        if hasattr(self, "_shape_combo"):
            shape = str(cfg.get("shape", "box"))
            idx = self._shape_combo.findText(shape)
            if idx >= 0:
                self._shape_combo.blockSignals(True)
                self._shape_combo.setCurrentIndex(idx)
                self._shape_combo.blockSignals(False)

        if hasattr(self, "_dim1_spin") and hasattr(self, "_dim2_spin"):
            dims = list(cfg.get("dims", [0.2, 0.2]))
            self._dim1_spin.blockSignals(True)
            self._dim2_spin.blockSignals(True)
            if len(dims) >= 1:
                self._dim1_spin.setValue(float(dims[0]))
            if len(dims) >= 2:
                self._dim2_spin.setValue(float(dims[1]))
            self._dim1_spin.blockSignals(False)
            self._dim2_spin.blockSignals(False)

        if hasattr(self, "_target_radius_spin"):
            self._target_radius_spin.blockSignals(True)
            self._target_radius_spin.setValue(float(cfg.get("target_radius", 15.0)))
            self._target_radius_spin.blockSignals(False)

        self._recompute_payload_physics()
        self._update_panel_summaries()

    def apply_system_mode(self, mode: str) -> None:
        """No-op stub — fidelity UI removed in rebuild. Called by MainWindow."""
        pass

    def update_target_status(self, position) -> None:
        """Called by MainWindow when target position changes on the tactical map."""
        if position is None:
            self._target_status_label.setText("⬤ Target: Not Set")
            self._target_status_label.setStyleSheet("color: #ff4444; font-size: 12px;")
        else:
            x, y, z = float(position[0]), float(position[1]), float(position[2])
            self._target_status_label.setText(
                f"⬤ Target: ENU ({x:.1f}, {y:.1f}, {z:.1f}) m"
            )
            self._target_status_label.setStyleSheet("color: #00ff88; font-size: 12px;")

    def load_from_snapshot(self, snapshot: dict) -> None:
        """Update display from snapshot. Does not set dirty."""
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
