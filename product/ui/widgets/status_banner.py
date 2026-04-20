from enum import IntEnum
from PySide6.QtCore import Qt, Signal, QTimer, QRect
from PySide6.QtGui import QPainter, QBrush, QColor, QFont, QFontDatabase, QPen
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout


def _select_monospace_family() -> str:
    families = set(QFontDatabase.families())
    if "Consolas" in families:
        return "Consolas"
    if "Courier New" in families:
        return "Courier New"
    return "Courier"


_MONO_FAMILY = None


def _mono_family() -> str:
    global _MONO_FAMILY
    if _MONO_FAMILY is None:
        _MONO_FAMILY = _select_monospace_family()
    return _MONO_FAMILY


class DropStatus(IntEnum):
    NO_DROP = 0
    APPROACH_CORRIDOR = 1
    IN_DROP_ZONE = 2
    DROP_NOW = 3


class DropReason(IntEnum):
    NONE = 0
    MISSION_PARAMS_NOT_SET = 1
    UAV_TOO_FAR = 2
    WIND_EXCEEDED = 3


class StatusBannerWidget(QWidget):
    navigate_to_tab = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(320, 64)

        self._current_status = DropStatus.NO_DROP
        self._current_reason = DropReason.NONE
        self._blink_visible = True

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet(
            "color: white; font-weight: bold; font-size: 13pt; background-color: transparent;"
        )
        self._label.setGeometry(0, 0, 320, 32)

        self._advisory_label = QLabel(self)
        self._advisory_label.setAlignment(Qt.AlignCenter)
        self._advisory_label.setStyleSheet(
            "color: white; font-size: 9pt; background-color: transparent;"
        )
        self._advisory_label.setGeometry(0, 30, 320, 28)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(1000)
        self._blink_timer.timeout.connect(self._toggle_advisory_blink)

        self._update_display()

    def set_status(self, status: DropStatus, reason: DropReason = DropReason.NONE):
        if self._current_status != status or self._current_reason != reason:
            self._current_status = status
            self._current_reason = reason
            if status == DropStatus.NO_DROP and reason == DropReason.MISSION_PARAMS_NOT_SET:
                if not self._blink_timer.isActive():
                    self._blink_timer.start()
            else:
                if self._blink_timer.isActive():
                    self._blink_timer.stop()
                self._blink_visible = True
                self._advisory_label.setVisible(True)
            self._update_display()
            self.update()

    @property
    def current_status(self) -> DropStatus:
        return self._current_status

    @property
    def current_reason(self) -> DropReason:
        return self._current_reason

    def _update_display(self):
        if self._current_status == DropStatus.NO_DROP:
            bg_color = QColor("#CC2200")
            text = "NO DROP"
            text_color = "white"
            if self._current_reason == DropReason.MISSION_PARAMS_NOT_SET:
                advisory = "Mission parameters not set — click to configure"
                advisory_color = "#FFDD00"
            elif self._current_reason == DropReason.UAV_TOO_FAR:
                advisory = "Outside drop corridor — adjust heading"
                advisory_color = "white"
            elif self._current_reason == DropReason.WIND_EXCEEDED:
                advisory = "Wind envelope exceeded — hold position"
                advisory_color = "white"
            else:
                advisory = ""
                advisory_color = "white"
        elif self._current_status == DropStatus.APPROACH_CORRIDOR:
            bg_color = QColor("#FF8C00")
            text = "APPROACH CORRIDOR"
            text_color = "white"
            advisory = "Intercept heading — maintain altitude"
            advisory_color = "white"
        elif self._current_status == DropStatus.IN_DROP_ZONE:
            bg_color = QColor("#00AA44")
            text = "IN DROP ZONE"
            text_color = "white"
            advisory = "Confirm release conditions"
            advisory_color = "white"
        elif self._current_status == DropStatus.DROP_NOW:
            bg_color = QColor("#00FF66")
            text = "DROP NOW"
            text_color = "black"
            advisory = "Release payload immediately"
            advisory_color = "black"
        else:
            bg_color = QColor("#CC2200")
            text = "UNKNOWN"
            text_color = "white"
            advisory = ""
            advisory_color = "white"

        bg_color.setAlpha(200)
        self._bg_color = bg_color
        self._label.setText(text)
        self._label.setStyleSheet(
            f"color: {text_color}; font-weight: bold; font-size: 13pt; background-color: transparent;"
        )
        self._advisory_label.setText(advisory)
        self._advisory_label.setStyleSheet(
            f"color: {advisory_color}; font-size: 9pt; background-color: transparent;"
        )

    def _toggle_advisory_blink(self):
        self._blink_visible = not self._blink_visible
        self._advisory_label.setVisible(self._blink_visible)

    def mousePressEvent(self, event):
        if (
            self._current_status == DropStatus.NO_DROP
            and self._current_reason == DropReason.MISSION_PARAMS_NOT_SET
        ):
            self.navigate_to_tab.emit(2)

    def wheelEvent(self, event):
        super().wheelEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(self._bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 6, 6)


_STATUS_COLOR = {
    DropStatus.NO_DROP: "#CC2200",
    DropStatus.APPROACH_CORRIDOR: "#FF8C00",
    DropStatus.IN_DROP_ZONE: "#00AA44",
    DropStatus.DROP_NOW: "#00FF66",
}

_CENTER_TINT = {
    DropStatus.NO_DROP: "#1a0000",
    DropStatus.APPROACH_CORRIDOR: "#1a0e00",
    DropStatus.IN_DROP_ZONE: "#001a08",
    DropStatus.DROP_NOW: "#002200",
}

_STATUS_TEXT = {
    DropStatus.NO_DROP: "NO DROP",
    DropStatus.APPROACH_CORRIDOR: "APPROACH CORRIDOR",
    DropStatus.IN_DROP_ZONE: "IN DROP ZONE",
    DropStatus.DROP_NOW: "DROP NOW",
}

_ADVISORY_TEXT = {
    (DropStatus.NO_DROP, DropReason.MISSION_PARAMS_NOT_SET):
        ("Mission parameters not set — click to configure", "#FFDD00"),
    (DropStatus.NO_DROP, DropReason.UAV_TOO_FAR):
        ("Outside drop corridor — adjust heading", "white"),
    (DropStatus.NO_DROP, DropReason.WIND_EXCEEDED):
        ("Wind envelope exceeded — hold position", "white"),
    (DropStatus.NO_DROP, DropReason.NONE): ("", "white"),
    (DropStatus.APPROACH_CORRIDOR, DropReason.NONE):
        ("Intercept heading — maintain altitude", "white"),
    (DropStatus.IN_DROP_ZONE, DropReason.NONE):
        ("Confirm release conditions", "white"),
    (DropStatus.DROP_NOW, DropReason.NONE):
        ("Release payload immediately", "white"),
}


class MissionStatusStrip(QWidget):
    """Persistent mission status strip — paintEvent-rendered GCS header.

    Military GCS reference: Apache IHADSS annunciator bar + QGroundControl
    status header. Three panels: LEFT guidance (HDG/DIST), CENTER status +
    advisory, RIGHT probability (P(HIT)/CEP50). Fixed 72 px; bottom border
    accent colored by DropStatus (static, not animated).
    """

    navigate_to_tab = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)

        self._status = DropStatus.NO_DROP
        self._reason = DropReason.MISSION_PARAMS_NOT_SET
        self._hdg_deg = None
        self._dist_m = None
        self._p_hit = None
        self._cep_m = None

        family = _mono_family()
        self._font_primary = QFont(family, 18, QFont.Bold)
        self._font_advisory = QFont(family, 10)
        self._font_value = QFont(family, 11)
        self._font_sub = QFont(family, 9)

    def update_status(self, status: DropStatus, reason: DropReason = DropReason.NONE):
        if self._status == status and self._reason == reason:
            return
        self._status = status
        self._reason = reason
        self.update()

    def update_guidance(self, heading_deg, dist_m, p_hit, cep_m):
        self._hdg_deg = heading_deg
        self._dist_m = dist_m
        self._p_hit = p_hit
        self._cep_m = cep_m
        self.update()

    @staticmethod
    def _fmt_hdg(v):
        return f"HDG: {v:.0f}\u00b0" if v is not None else "HDG: ---\u00b0"

    @staticmethod
    def _fmt_dist(v):
        return f"DIST: {v:.0f} m" if v is not None else "DIST: --- m"

    @staticmethod
    def _fmt_phit(v):
        return f"P(HIT): {v:.2f}" if v is not None else "P(HIT): ---"

    @staticmethod
    def _fmt_cep(v):
        if v is None or v > 999.9:
            return "CEP\u2085\u2080: --- m"
        return f"CEP\u2085\u2080: {v:.1f} m"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)

        w = self.width()
        h = self.height()
        left_w = int(w * 0.25)
        right_w = int(w * 0.25)
        center_w = w - left_w - right_w

        left_rect = QRect(0, 0, left_w, h)
        center_rect = QRect(left_w, 0, center_w, h)
        right_rect = QRect(left_w + center_w, 0, right_w, h)

        status_color_str = _STATUS_COLOR.get(self._status, "#CC2200")
        status_qcolor = QColor(status_color_str)
        center_tint = QColor(_CENTER_TINT.get(self._status, "#1a0000"))
        bg = QColor("#0a0a0a")

        # Panel backgrounds.
        painter.fillRect(left_rect, bg)
        painter.fillRect(center_rect, center_tint)
        painter.fillRect(right_rect, bg)

        pad = 12

        # LEFT: HDG / DIST, left-aligned, stacked.
        painter.setPen(QColor("white"))
        painter.setFont(self._font_value)
        hdg_rect = QRect(left_rect.x() + pad, 10, left_rect.width() - pad, 24)
        painter.drawText(hdg_rect, Qt.AlignLeft | Qt.AlignVCenter, self._fmt_hdg(self._hdg_deg))
        painter.setFont(self._font_sub)
        dist_rect = QRect(left_rect.x() + pad, 38, left_rect.width() - pad, 22)
        painter.drawText(dist_rect, Qt.AlignLeft | Qt.AlignVCenter, self._fmt_dist(self._dist_m))

        # CENTER: primary status + advisory, center-aligned.
        primary_text = _STATUS_TEXT.get(self._status, "UNKNOWN")
        painter.setPen(status_qcolor)
        painter.setFont(self._font_primary)
        primary_rect = QRect(center_rect.x(), 6, center_rect.width(), 34)
        painter.drawText(primary_rect, Qt.AlignCenter, primary_text)

        advisory, advisory_color_str = _ADVISORY_TEXT.get(
            (self._status, self._reason),
            _ADVISORY_TEXT.get((self._status, DropReason.NONE), ("", "white")),
        )
        painter.setPen(QColor(advisory_color_str))
        painter.setFont(self._font_advisory)
        advisory_rect = QRect(center_rect.x(), 40, center_rect.width(), 26)
        painter.drawText(advisory_rect, Qt.AlignCenter, advisory)

        # RIGHT: P(HIT) / CEP, right-aligned.
        painter.setPen(QColor("white"))
        painter.setFont(self._font_value)
        phit_rect = QRect(right_rect.x(), 10, right_rect.width() - pad, 24)
        painter.drawText(phit_rect, Qt.AlignRight | Qt.AlignVCenter, self._fmt_phit(self._p_hit))
        painter.setFont(self._font_sub)
        cep_rect = QRect(right_rect.x(), 38, right_rect.width() - pad, 22)
        painter.drawText(cep_rect, Qt.AlignRight | Qt.AlignVCenter, self._fmt_cep(self._cep_m))

        # Bottom border accent — 2 px, status-colored, static.
        pen = QPen(status_qcolor)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(0, h - 1, w, h - 1)

    def mousePressEvent(self, event):
        if (
            self._status == DropStatus.NO_DROP
            and self._reason == DropReason.MISSION_PARAMS_NOT_SET
        ):
            self.navigate_to_tab.emit(2)
        super().mousePressEvent(event)
