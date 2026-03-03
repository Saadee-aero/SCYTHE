"""Reusable Qt widgets for AIRDROP-X Phase 1 shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget


class NoWheelSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events so scrolling scrolls the window instead of changing value."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that ignores wheel events so scrolling scrolls the window instead of changing value."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelSlider(QSlider):
    """QSlider that ignores wheel events so scrolling scrolls the window instead of changing value."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class PlotAreaPlaceholder(QFrame):
    """Center plot area placeholder (FigureCanvasQTAgg will go here later)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("plotPlaceholder")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        label = QLabel("PLOT CANVAS PLACEHOLDER", self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("plotPlaceholderText")
        layout.addWidget(label)


class StatusStrip(QFrame):
    """Bottom status placeholder row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusStrip")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self.snapshot_label = QLabel("Snapshot ID: --", self)
        self.snapshot_label.setObjectName("statusLabel")
        layout.addWidget(self.snapshot_label)

        self.telemetry_label = QLabel("Telemetry: Placeholder", self)
        self.telemetry_label.setObjectName("statusLabel")
        layout.addWidget(self.telemetry_label)
