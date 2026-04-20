"""Matplotlib canvas widgets for SCYTHE Qt migration.

NOTE: The main window does NOT use this module. It uses product.ui.qt_bridge
with one figure/canvas per tab and product.ui.tabs.* renderers. This module
provides an alternate standalone ImpactDispersionCanvas (with fade animations)
for possible future use (e.g. a dedicated dispersion widget). Do not remove
without updating any code that may import it.
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Ellipse


class ImpactDispersionCanvas(FigureCanvasQTAgg):
    """Alternate impact-dispersion canvas (fade animations). Not used by MainWindow."""

    def __init__(self, parent=None) -> None:
        self.figure = Figure(figsize=(6.0, 5.0), facecolor="#0B0F0B")
        self.ax = self.figure.add_subplot(111)
        self.current_mode = "standard"
        self._last_snapshot = None
        self._last_draw_ts = 0.0
        super().__init__(self.figure)
        if parent is not None:
            self.setParent(parent)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self.opacity_effect)
        self._fade_out_anim = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self._fade_in_anim = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self._fade_out_anim.setDuration(150)
        self._fade_in_anim.setDuration(150)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_in_anim.setStartValue(0.0)
        self._fade_in_anim.setEndValue(1.0)
        self._fade_out_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_in_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_out_anim.finished.connect(self._on_fade_out_finished)
        self._pending_snapshot = None

        self._style_axes()

    def _style_axes(self) -> None:
        self.ax.set_facecolor("#0B0F0B")
        self.ax.grid(True, color="#173017", linewidth=0.8, alpha=0.55)
        self.ax.tick_params(colors="#6C8F6A")
        self.ax.xaxis.label.set_color("#6C8F6A")
        self.ax.yaxis.label.set_color("#6C8F6A")
        self.ax.set_title("IMPACT DISPERSION", color="#2CFF05", pad=10)
        self.ax.set_xlabel("X (m)")
        self.ax.set_ylabel("Y (m)")
        for spine in self.ax.spines.values():
            spine.set_color("#234023")

    def set_mode(self, mode: str) -> None:
        mode_norm = str(mode).strip().lower()
        if mode_norm not in ("standard", "advanced"):
            return
        self.current_mode = mode_norm
        self.redraw_last_snapshot()

    def redraw_last_snapshot(self) -> None:
        if self._last_snapshot is None:
            self.ax.clear()
            self._style_axes()
            self.ax.set_xlim(-20, 20)
            self.ax.set_ylim(-20, 20)
            self.ax.set_aspect("equal", adjustable="box")
            _now = time.monotonic()
            if _now - self._last_draw_ts >= 0.5:
                self._last_draw_ts = _now
                self.draw_idle()
            return
        self.plot_from_snapshot(self._last_snapshot)

    def smooth_update(self, snapshot) -> None:
        self._pending_snapshot = dict(snapshot or {})
        current_opacity = float(self.opacity_effect.opacity())
        self._fade_out_anim.stop()
        self._fade_in_anim.stop()
        self._fade_out_anim.setStartValue(current_opacity)
        self._fade_out_anim.start()

    def _on_fade_out_finished(self) -> None:
        snapshot = self._pending_snapshot
        self._pending_snapshot = None
        if snapshot is None:
            self._fade_in_anim.start()
            return
        self.plot_from_snapshot(snapshot)
        self._fade_in_anim.start()

    @staticmethod
    def _ellipse_color(P_hit: float | None) -> str:
        if P_hit is None:
            return "#2CFF05"
        if P_hit > 0.80:
            return "#2CFF05"
        if P_hit >= 0.60:
            return "#ffaa00"
        return "#ff4444"

    def plot_from_snapshot(self, snapshot) -> None:
        self._last_snapshot = dict(snapshot or {})
        self.ax.clear()
        self._style_axes()

        impact_points = np.asarray(snapshot.get("impact_points", []), dtype=float)
        target_center = np.asarray(snapshot.get("target_position", (0.0, 0.0)), dtype=float).flatten()[:2]
        target_radius = float(snapshot.get("target_radius", 0.0) or 0.0)
        wind_vector = snapshot.get("wind_vector")
        P_hit = snapshot.get("P_hit")
        cep50 = float(snapshot.get("cep50", 0.0) or 0.0)

        target = Circle(
            tuple(target_center),
            target_radius,
            fill=False,
            edgecolor="#00FF41",
            linewidth=2.4,
        )
        self.ax.add_patch(target)

        mean = impact_points.mean(axis=0) if impact_points.shape[0] > 0 else target_center.copy()

        self.ax.scatter(
            mean[0],
            mean[1],
            marker="x",
            s=100,
            c="#F3F5F3",
            linewidths=2.2,
            zorder=5,
        )

        ellipse_color = self._ellipse_color(P_hit if isinstance(P_hit, (int, float)) else None)
        if impact_points.shape[0] >= 2:
            try:
                cov = np.cov(impact_points.T)
                eigvals, eigvecs = np.linalg.eigh(cov)
                order = eigvals.argsort()[::-1]
                eigvals = eigvals[order]
                eigvecs = eigvecs[:, order]
                semi_major_2sigma = 2.0 * np.sqrt(max(float(eigvals[0]), 0.0))
                semi_minor_2sigma = 2.0 * np.sqrt(max(float(eigvals[1]), 0.0))
                ellipse = Ellipse(
                    xy=tuple(mean),
                    width=2.0 * semi_major_2sigma,
                    height=2.0 * semi_minor_2sigma,
                    angle=float(np.degrees(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))),
                    fill=False,
                    edgecolor=ellipse_color,
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.9,
                )
                self.ax.add_patch(ellipse)
            except Exception:
                pass

        wind_mag = 0.0
        if wind_vector is not None:
            wind = np.asarray(wind_vector, dtype=float).reshape(2)
            wind_mag = float(np.linalg.norm(wind))
            if wind_mag > 0:
                direction = wind / wind_mag
                arrow_length = 6.0
                vec = direction * arrow_length
                self.ax.arrow(
                    target_center[0],
                    target_center[1],
                    vec[0],
                    vec[1],
                    color="#E6B800",
                    width=0.18,
                    head_width=1.0,
                    head_length=1.4,
                    length_includes_head=True,
                    zorder=4,
                )

        # Standard mode: minimal overlays only.
        if self.current_mode == "standard":
            dx = float(mean[0] - target_center[0])
            dy = float(mean[1] - target_center[1])
            offset = float(np.hypot(dx, dy))
            if offset > 0.2:
                self.ax.arrow(
                    target_center[0],
                    target_center[1],
                    dx,
                    dy,
                    color="#f0f0f0",
                    width=0.10,
                    head_width=0.8,
                    head_length=1.0,
                    length_includes_head=True,
                    zorder=5,
                )

            self.ax.text(
                0.5,
                0.02,
                f"HIT: {(float(P_hit) * 100.0 if isinstance(P_hit, (int, float)) else 0.0):.1f}% | "
                f"OFFSET: {offset:.2f} m | WIND: {wind_mag:.1f} m/s",
                transform=self.ax.transAxes,
                ha="center",
                va="bottom",
                fontsize=8,
                color="#6C8F6A",
                family="monospace",
                zorder=10,
            )
        else:
            # Advanced mode: full layers + legend.
            if impact_points.shape[0] > 0:
                self.ax.scatter(
                    impact_points[:, 0],
                    impact_points[:, 1],
                    s=16,
                    c="#00FF41",
                    alpha=0.45,
                    edgecolors="none",
                    zorder=2,
                )
            if cep50 > 0:
                cep = Circle(
                    tuple(mean),
                    cep50,
                    fill=False,
                    edgecolor="#4a7c4a",
                    linestyle="--",
                    linewidth=1.2,
                    alpha=0.85,
                )
                self.ax.add_patch(cep)

            handles = [
                Line2D([0], [0], marker="o", color="none", markerfacecolor="#00FF41", markeredgecolor="none", markersize=6, label="Impacts"),
                Line2D([0], [0], marker="o", color="none", markerfacecolor="none", markeredgecolor="#00FF41", markersize=6, label="Target"),
                Line2D([0], [0], marker="x", color="#F3F5F3", markersize=8, label="Mean"),
                Line2D([0], [0], linestyle="--", color="#4a7c4a", linewidth=1.2, label="CEP"),
                Line2D([0], [0], linestyle="--", color=ellipse_color, linewidth=1.4, label="2-sigma Ellipse"),
                Line2D([0], [0], color="#E6B800", linewidth=2.0, label="Wind"),
            ]
            leg = self.ax.legend(
                handles=handles,
                loc="lower left",
                fontsize=7,
                frameon=True,
                framealpha=0.55,
                facecolor=(0.06, 0.08, 0.06),
                edgecolor="none",
                labelcolor="#b0d0b0",
            )
            leg.set_zorder(20)

        # Keep a stable but data-aware view window.
        if impact_points.shape[0] > 0:
            xs = impact_points[:, 0]
            ys = impact_points[:, 1]
            xmin = min(float(xs.min()), float(target_center[0] - target_radius))
            xmax = max(float(xs.max()), float(target_center[0] + target_radius))
            ymin = min(float(ys.min()), float(target_center[1] - target_radius))
            ymax = max(float(ys.max()), float(target_center[1] + target_radius))
            span = max(xmax - xmin, ymax - ymin, 10.0)
            pad = 0.25 * span
            cx = 0.5 * (xmin + xmax)
            cy = 0.5 * (ymin + ymax)
            self.ax.set_xlim(cx - 0.5 * span - pad, cx + 0.5 * span + pad)
            self.ax.set_ylim(cy - 0.5 * span - pad, cy + 0.5 * span + pad)
        else:
            self.ax.set_xlim(-20, 20)
            self.ax.set_ylim(-20, 20)
        self.ax.set_aspect("equal", adjustable="box")
        _now = time.monotonic()
        if _now - self._last_draw_ts >= 0.5:
            self._last_draw_ts = _now
            self.draw_idle()
