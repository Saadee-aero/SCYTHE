from __future__ import annotations

import math
import random
from typing import Iterable, List, Tuple

from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import QBrush, QFont, QImage, QPen, QPixmap, QPolygonF, QPainter, QColor, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QWidget,
)

from product.ui.map_transform import MapTransform
from product.ui.widgets.status_banner import DropStatus

SIGMA_68 = 1.0    # 1-sigma, 68.27% confidence for 2D Gaussian
SIGMA_95 = 2.448  # sqrt(chi2.ppf(0.95, df=2)) = sqrt(5.991)


class CameraFeedLayer(QWidget):
    """Viewport-fill camera feed layer. Bottom of viewport-child z-order.

    Raw pixel overlay — no georeferencing. Silent fail on None frame.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._pixmap: QPixmap | None = None
        self._needs_redraw = True

    def update_frame(self, image) -> None:
        if image is None:
            return
        try:
            pm = QPixmap.fromImage(image)
            if pm.isNull():
                return
            self._pixmap = pm
            self._needs_redraw = True
            self.update()
        except Exception:
            return

    def paintEvent(self, event) -> None:
        if not self._needs_redraw:
            return
        painter = QPainter(self)
        if self._pixmap is None or self._pixmap.isNull():
            painter.fillRect(self.rect(), QColor(0, 0, 0, 40))
            font = QFont("Consolas", 10)
            painter.setFont(font)
            painter.setPen(QColor("#444444"))
            painter.drawText(self.rect(), Qt.AlignCenter, "NO CAMERA FEED")
            self._needs_redraw = False
            return
        painter.drawPixmap(0, 0, self._pixmap)
        self._needs_redraw = False


class WindIndicatorLayer(QWidget):
    """Viewport-fixed wind indicator: arrow + speed label at bottom-left.

    Per §14 Phase 4.1: fixed screen position, arrow length 20-80 px proportional
    to wind speed, ENU direction Y-flipped for Qt screen coords, white 9pt label.
    """

    _ARROW_MIN_PX = 20.0
    _ARROW_MAX_PX = 80.0
    _SPEED_TO_PX = 10.0  # m/s -> pixels scale factor

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._wind_x = 0.0
        self._wind_y = 0.0
        self._speed = 0.0
        self.setFixedSize(120, 100)
        self.hide()

    def update_wind(self, wind_x: float, wind_y: float) -> None:
        self._wind_x = float(wind_x)
        self._wind_y = float(wind_y)
        self._speed = math.hypot(self._wind_x, self._wind_y)
        self.show()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            cx = self.width() / 2.0
            cy = self.height() / 2.0 - 8.0
            length = min(self._ARROW_MAX_PX,
                         max(self._ARROW_MIN_PX, self._speed * self._SPEED_TO_PX))
            # ENU -> Qt screen: Y-flip. Downwind direction = (wind_x, wind_y).
            angle = math.atan2(-self._wind_y, self._wind_x) if self._speed > 1e-6 else 0.0
            x1 = cx - 0.5 * length * math.cos(angle)
            y1 = cy - 0.5 * length * math.sin(angle)
            x2 = cx + 0.5 * length * math.cos(angle)
            y2 = cy + 0.5 * length * math.sin(angle)
            pen = QPen(QColor("#ffffff"), 2)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            # Arrowhead at (x2, y2).
            head = 8.0
            left = angle + math.radians(150)
            right = angle - math.radians(150)
            poly = QPolygonF([
                QPointF(x2, y2),
                QPointF(x2 + head * math.cos(left), y2 + head * math.sin(left)),
                QPointF(x2 + head * math.cos(right), y2 + head * math.sin(right)),
            ])
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawPolygon(poly)
            # Label below arrow.
            p.setFont(QFont("Consolas", 9))
            p.setPen(QColor("#ffffff"))
            label = f"{self._speed:.1f} m/s"
            p.drawText(QRectF(0.0, self.height() - 18.0, float(self.width()), 16.0),
                       int(Qt.AlignHCenter | Qt.AlignVCenter), label)
        finally:
            p.end()


class UAVMarker(QGraphicsPolygonItem):
    """Triangle marker representing UAV heading."""

    def __init__(self) -> None:
        poly = QPolygonF(
            [
                QPointF(15.0, 0.0),
                QPointF(-10.0, -7.0),
                QPointF(-10.0, 7.0),
            ]
        )
        super().__init__(poly)
        self.setPen(QPen(QColor("#00ff41"), 1.5))
        self.setBrush(QBrush(QColor("#00ff41")))
        self.setTransformOriginPoint(0.0, 0.0)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)

    def set_position(self, x: float, y: float) -> None:
        self.setPos(float(x), float(y))

    def set_heading(self, angle_deg: float) -> None:
        self.setRotation(float(angle_deg))


class ImpactEllipseLayer:
    """Manages the 68/95/outer safety ellipses."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._ellipse_68 = QGraphicsEllipseItem()
        self._ellipse_95 = QGraphicsEllipseItem()

        green = QColor("#00AA44")
        amber = QColor("#FF8C00")
        self._ellipse_68.setPen(QPen(green, 2))
        self._ellipse_95.setPen(QPen(amber, 2))

        green_brush = QColor(green)
        green_brush.setAlpha(120)
        amber_brush = QColor(amber)
        amber_brush.setAlpha(80)
        self._ellipse_68.setBrush(QBrush(green_brush))
        self._ellipse_95.setBrush(QBrush(amber_brush))

        self._ellipse_68.setOpacity(1.0)
        self._ellipse_95.setOpacity(1.0)

        self._ellipse_68.setZValue(2)
        self._ellipse_95.setZValue(2)
        scene.addItem(self._ellipse_68)
        scene.addItem(self._ellipse_95)

    def update(self, mean_x: float, mean_y: float, a: float, b: float, angle: float) -> None:
        self._update_item(self._ellipse_68, mean_x, mean_y, a, b, angle, scale=SIGMA_68)
        self._update_item(self._ellipse_95, mean_x, mean_y, a, b, angle, scale=SIGMA_95)

    @staticmethod
    def _update_item(
        item: QGraphicsEllipseItem,
        mean_x: float,
        mean_y: float,
        a: float,
        b: float,
        angle: float,
        scale: float,
    ) -> None:
        ax = float(a) * scale
        by = float(b) * scale
        cx = float(mean_x)
        cy = float(mean_y)
        rect = QRectF(cx - ax, cy - by, ax * 2.0, by * 2.0)
        item.setRect(rect)
        item.setTransformOriginPoint(cx, cy)
        item.setRotation(float(angle))


class TargetMarker(QGraphicsItemGroup):
    """Crosshair target marker."""

    def __init__(self) -> None:
        super().__init__()
        circle = QGraphicsEllipseItem(-10.0, -10.0, 20.0, 20.0)
        circle.setPen(QPen(QColor("#ffff33"), 2))
        circle.setBrush(QBrush(Qt.transparent))
        h = QGraphicsLineItem(-10.0, 0.0, 10.0, 0.0)
        h.setPen(QPen(QColor("#ffff33"), 2))
        v = QGraphicsLineItem(0.0, -10.0, 0.0, 10.0)
        v.setPen(QPen(QColor("#ffff33"), 2))
        self.addToGroup(circle)
        self.addToGroup(h)
        self.addToGroup(v)
        self.setZValue(5)

    def set_position(self, x: float, y: float) -> None:
        self.setPos(float(x), float(y))


class CorridorLayer(QGraphicsPolygonItem):
    """Release corridor polygon with centerline."""

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__()
        border = QColor("#00AA44")
        border.setAlpha(180)
        fill = QColor("#00AA44")
        fill.setAlpha(40)
        self.setPen(QPen(border, 1))
        self.setBrush(QBrush(fill))
        self.setZValue(4)
        self.setZValue(2)
        scene.addItem(self)

        self._centerline = QGraphicsLineItem()
        pen = QPen(QColor("#ffffff"), 2)
        pen.setStyle(Qt.DashLine)
        self._centerline.setPen(pen)
        self._centerline.setZValue(4)
        scene.addItem(self._centerline)

        self._entry_marker = QGraphicsPolygonItem()
        self._entry_marker.setPen(QPen(QColor("#00ffff"), 2))
        self._entry_marker.setBrush(QBrush(QColor("#00ffff")))
        self._entry_marker.setZValue(4)
        scene.addItem(self._entry_marker)

        self._centerline_points: Tuple[QPointF, QPointF] | None = None
        self._collapsed = False
        self._entry_point: QPointF | None = None

    def update_corridor(self, points: Iterable[Tuple[float, float]]) -> None:
        pts = list(points)
        if len(pts) < 3:
            self.setVisible(False)
            self._centerline.setVisible(False)
            self._centerline_points = None
            self._collapsed = True
            return
        area = self._polygon_area(pts)
        if area < 5.0:
            self.setVisible(False)
            self._centerline.setVisible(False)
            self._centerline_points = None
            self._collapsed = True
            return
        self._collapsed = False
        self.setVisible(True)
        poly = QPolygonF([QPointF(float(x), float(y)) for x, y in pts])
        self.setPolygon(poly)
        self._update_centerline(pts)
        self._update_entry_marker(pts)

    def _update_centerline(self, points: Iterable[Tuple[float, float]]) -> None:
        pts = [QPointF(float(x), float(y)) for x, y in points]
        if len(pts) < 4:
            self._centerline.setVisible(False)
            self._centerline_points = None
            return
        a0 = QPointF((pts[0].x() + pts[1].x()) * 0.5, (pts[0].y() + pts[1].y()) * 0.5)
        a1 = QPointF((pts[2].x() + pts[3].x()) * 0.5, (pts[2].y() + pts[3].y()) * 0.5)
        self._centerline.setLine(a0.x(), a0.y(), a1.x(), a1.y())
        self._centerline.setVisible(True)
        self._centerline_points = (a0, a1)

    def _update_entry_marker(self, points: Iterable[Tuple[float, float]]) -> None:
        pts = [QPointF(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            self._entry_marker.setVisible(False)
            self._entry_point = None
            return
        entry = QPointF((pts[0].x() + pts[1].x()) * 0.5, (pts[0].y() + pts[1].y()) * 0.5)
        tri = QPolygonF(
            [
                QPointF(entry.x(), entry.y() + 6.0),
                QPointF(entry.x() - 5.0, entry.y() - 4.0),
                QPointF(entry.x() + 5.0, entry.y() - 4.0),
            ]
        )
        self._entry_marker.setPolygon(tri)
        self._entry_marker.setVisible(True)
        self._entry_point = entry

    def set_centerline_visible(self, visible: bool) -> None:
        self._centerline.setVisible(visible)

    def centerline_points(self) -> Tuple[QPointF, QPointF] | None:
        return self._centerline_points

    def entry_point(self) -> QPointF | None:
        return self._entry_point

    def is_collapsed(self) -> bool:
        return self._collapsed

    @staticmethod
    def _polygon_area(points: Iterable[Tuple[float, float]]) -> float:
        pts = list(points)
        area = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            area += x1 * y2 - x2 * y1
        return abs(area) * 0.5


class GuidanceArrow:
    """Line + arrow head guidance indicator."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._line = QGraphicsLineItem()
        self._line.setPen(QPen(QColor("#ffffff"), 2))
        self._line.setZValue(4)
        scene.addItem(self._line)

        self._head = QGraphicsPolygonItem()
        self._head.setPen(QPen(QColor("#ffffff"), 2))
        self._head.setBrush(QBrush(QColor("#ffffff")))
        self._head.setZValue(4)
        scene.addItem(self._head)

    def update(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._line.setLine(float(x1), float(y1), float(x2), float(y2))
        self._update_head(x1, y1, x2, y2)

    def set_visible(self, visible: bool) -> None:
        self._line.setVisible(visible)
        self._head.setVisible(visible)

    def _update_head(self, x1: float, y1: float, x2: float, y2: float) -> None:
        angle = math.atan2(float(y2) - float(y1), float(x2) - float(x1))
        size = 10.0
        left = angle + math.radians(150)
        right = angle - math.radians(150)
        p1 = QPointF(float(x2), float(y2))
        p2 = QPointF(float(x2) + size * math.cos(left), float(y2) + size * math.sin(left))
        p3 = QPointF(float(x2) + size * math.cos(right), float(y2) + size * math.sin(right))
        self._head.setPolygon(QPolygonF([p1, p2, p3]))


class BoundaryIndicator:
    """Arrow + label when UAV is outside focus window."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._arrow = QGraphicsPolygonItem()
        self._arrow.setPen(QPen(QColor("#00ffff"), 2))
        self._arrow.setBrush(QBrush(QColor("#00ffff")))
        self._arrow.setZValue(100)
        scene.addItem(self._arrow)

        self._label = QGraphicsTextItem("UAV OUTSIDE VIEW")
        self._label.setFont(QFont("Consolas", 10))
        self._label.setDefaultTextColor(QColor("#00ffff"))
        self._label.setZValue(100)
        self._label.setTransform(QTransform().scale(1.0, -1.0))
        scene.addItem(self._label)

        self.set_visible(False)

    def set_visible(self, visible: bool) -> None:
        self._arrow.setVisible(visible)
        self._label.setVisible(visible)

    def update(self, uav: QPointF, rect: QRectF) -> None:
        if rect.contains(uav):
            self.set_visible(False)
            return
        self.set_visible(True)
        cx, cy = rect.center().x(), rect.center().y()
        dx = uav.x() - cx
        dy = uav.y() - cy
        if dx == 0 and dy == 0:
            return
        t_values = []
        if dx != 0:
            t_values.append((rect.left() - cx) / dx)
            t_values.append((rect.right() - cx) / dx)
        if dy != 0:
            t_values.append((rect.top() - cy) / dy)
            t_values.append((rect.bottom() - cy) / dy)
        t_candidates = [t for t in t_values if t > 0]
        if not t_candidates:
            return
        t = min(t_candidates)
        px = cx + dx * t
        py = cy + dy * t
        angle = math.atan2(dy, dx)
        size = 10.0
        tip = QPointF(px, py)
        left = QPointF(px - size * math.cos(angle - math.radians(25)),
                       py - size * math.sin(angle - math.radians(25)))
        right = QPointF(px - size * math.cos(angle + math.radians(25)),
                        py - size * math.sin(angle + math.radians(25)))
        self._arrow.setPolygon(QPolygonF([tip, left, right]))
        self._label.setPos(px + 6.0, py + 6.0)


class DriftArrow:
    """Drift arrow from release point to mean impact."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._line = QGraphicsLineItem()
        self._line.setPen(QPen(QColor("#ff9933"), 2))
        self._line.setZValue(4)
        scene.addItem(self._line)

        self._head = QGraphicsPolygonItem()
        self._head.setPen(QPen(QColor("#ff9933"), 2))
        self._head.setBrush(QBrush(QColor("#ff9933")))
        self._head.setZValue(4)
        scene.addItem(self._head)

        self._label = QGraphicsTextItem("")
        self._label.setFont(QFont("Consolas", 9))
        self._label.setDefaultTextColor(QColor("#ff9933"))
        self._label.setZValue(100)
        self._label.setTransform(QTransform().scale(1.0, -1.0))
        scene.addItem(self._label)

    def update(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._line.setLine(float(x1), float(y1), float(x2), float(y2))
        self._update_head(x1, y1, x2, y2)
        mx = (float(x1) + float(x2)) * 0.5
        my = (float(y1) + float(y2)) * 0.5
        self._label.setPlainText("DRIFT VECTOR")
        self._label.setPos(mx + 6.0, my + 6.0)

    def set_visible(self, visible: bool) -> None:
        self._line.setVisible(visible)
        self._head.setVisible(visible)
        self._label.setVisible(visible)

    def _update_head(self, x1: float, y1: float, x2: float, y2: float) -> None:
        angle = math.atan2(float(y2) - float(y1), float(x2) - float(x1))
        size = 8.0
        left = angle + math.radians(150)
        right = angle - math.radians(150)
        p1 = QPointF(float(x2), float(y2))
        p2 = QPointF(float(x2) + size * math.cos(left), float(y2) + size * math.sin(left))
        p3 = QPointF(float(x2) + size * math.cos(right), float(y2) + size * math.sin(right))
        self._head.setPolygon(QPolygonF([p1, p2, p3]))


class ImpactScatterLayer:
    """Preallocated scatter points for Monte Carlo impacts."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._points: List[QGraphicsEllipseItem] = []
        self._max = 200
        pen = QPen(QColor("#ffffff"))
        brush = QBrush(QColor("#ffffff"))
        # Persistent items: create exactly 200 once at init.
        rng = random.Random(42)
        for _ in range(self._max):
            item = QGraphicsEllipseItem(-2.0, -2.0, 4.0, 4.0)
            shade = rng.randint(200, 255)
            color = QColor(shade, shade, shade)
            pen = QPen(color)
            brush = QBrush(color)
            item.setPen(pen)
            item.setBrush(brush)
            item.setOpacity(0.5)
            item.setZValue(3)
            item.setVisible(False)
            scene.addItem(item)
            self._points.append(item)

    def update_scatter(self, points: Iterable[Tuple[float, float]]) -> None:
        # Persistent items: update only geometry/visibility (no add/remove).
        pts = list(points)
        count = min(len(pts), self._max)
        for i in range(self._max):
            item = self._points[i]
            if i < count:
                x, y = pts[i]
                item.setPos(float(x), float(y))
                item.setVisible(True)
            else:
                item.setVisible(False)

    def set_visible(self, visible: bool) -> None:
        for item in self._points:
            item.setVisible(visible)


class UAVTrailLayer:
    """UAV path history (last 50 positions)."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._max = 50
        self._points: List[QPointF | None] = [None] * self._max
        self._index = 0
        self._count = 0
        self._segments: List[QGraphicsLineItem] = []
        pen = QPen(QColor("#00ffff"), 2)
        for _ in range(self._max - 1):
            seg = QGraphicsLineItem()
            seg.setPen(pen)
            seg.setZValue(6)
            seg.setOpacity(0.1)
            seg.setVisible(False)
            scene.addItem(seg)
            self._segments.append(seg)

    def update(self, x: float, y: float) -> None:
        pt = QPointF(float(x), float(y))
        self._points[self._index] = pt
        self._index = (self._index + 1) % self._max
        self._count = min(self._count + 1, self._max)
        ordered: List[QPointF] = []
        if self._count < self._max:
            for i in range(self._count):
                if self._points[i] is not None:
                    ordered.append(self._points[i])
        else:
            for i in range(self._max):
                idx = (self._index + i) % self._max
                p = self._points[idx]
                if p is not None:
                    ordered.append(p)
        for seg in self._segments:
            seg.setVisible(False)
        if len(ordered) < 2:
            return
        total_segs = len(ordered) - 1
        for i in range(total_segs):
            seg = self._segments[i]
            p1 = ordered[i]
            p2 = ordered[i + 1]
            seg.setLine(p1.x(), p1.y(), p2.x(), p2.y())
            t = i / max(total_segs - 1, 1)
            opacity = 0.6 - (0.6 - 0.1) * (1.0 - t)
            seg.setOpacity(max(0.1, min(0.6, opacity)))
            seg.setVisible(True)


class WindFieldLayer:
    """Grid of wind arrows across the map."""

    def __init__(self, scene: QGraphicsScene, spacing: float = 150.0) -> None:
        self._arrows: List[Tuple[QGraphicsLineItem, QGraphicsPolygonItem]] = []
        extent = 1000.0
        pen = QPen(QColor("#77bfff"), 1)
        for x in range(int(-extent), int(extent) + 1, int(spacing)):
            for y in range(int(-extent), int(extent) + 1, int(spacing)):
                line = QGraphicsLineItem()
                line.setPen(pen)
                line.setOpacity(0.5)
                line.setZValue(1)
                head = QGraphicsPolygonItem()
                head.setPen(pen)
                head.setBrush(QBrush(QColor("#77bfff")))
                head.setOpacity(0.5)
                head.setZValue(1)
                scene.addItem(line)
                scene.addItem(head)
                self._arrows.append((line, head))
        self._positions = [(x, y) for x in range(int(-extent), int(extent) + 1, int(spacing))
                           for y in range(int(-extent), int(extent) + 1, int(spacing))]
        self._main_line = QGraphicsLineItem()
        self._main_line.setPen(pen)
        self._main_line.setOpacity(0.6)
        self._main_line.setZValue(2)
        self._main_head = QGraphicsPolygonItem()
        self._main_head.setPen(pen)
        self._main_head.setBrush(QBrush(QColor("#77bfff")))
        self._main_head.setOpacity(0.6)
        self._main_head.setZValue(2)
        scene.addItem(self._main_line)
        scene.addItem(self._main_head)

    def update_field(self, wind_x: float, wind_y: float, zoom_level: float, target: Tuple[float, float] | None) -> None:
        speed = math.hypot(float(wind_x), float(wind_y))
        length = min(80.0, speed * 10.0)
        length = max(10.0, length)
        angle = math.atan2(float(wind_y), float(wind_x))
        dx = length * math.cos(angle)
        dy = length * math.sin(angle)
        if zoom_level < 0.8:
            for line, head in self._arrows:
                line.setVisible(False)
                head.setVisible(False)
            if target is not None:
                x, y = target
                self._main_line.setLine(x, y, x + dx, y + dy)
                self._update_head(self._main_head, x, y, x + dx, y + dy)
                self._main_line.setVisible(True)
                self._main_head.setVisible(True)
            else:
                self._main_line.setVisible(False)
                self._main_head.setVisible(False)
            return
        self._main_line.setVisible(False)
        self._main_head.setVisible(False)
        use_sparse = zoom_level < 1.5
        for (line, head), (x, y) in zip(self._arrows, self._positions):
            if use_sparse and (x % 300 != 0 or y % 300 != 0):
                line.setVisible(False)
                head.setVisible(False)
                continue
            x2 = x + dx
            y2 = y + dy
            line.setLine(x, y, x2, y2)
            self._update_head(head, x, y, x2, y2)
            line.setVisible(True)
            head.setVisible(True)

    @staticmethod
    def _update_head(head: QGraphicsPolygonItem, x1: float, y1: float, x2: float, y2: float) -> None:
        angle = math.atan2(float(y2) - float(y1), float(x2) - float(x1))
        size = 6.0
        left = angle + math.radians(150)
        right = angle - math.radians(150)
        p1 = QPointF(float(x2), float(y2))
        p2 = QPointF(float(x2) + size * math.cos(left), float(y2) + size * math.sin(left))
        p3 = QPointF(float(x2) + size * math.cos(right), float(y2) + size * math.sin(right))
        head.setPolygon(QPolygonF([p1, p2, p3]))


class ImpactHeatmapLayer:
    """Heatmap tiles for impact probability density."""

    def __init__(self, scene: QGraphicsScene, tile_size: float = 5.0) -> None:
        self._scene = scene
        self._tile_size = tile_size
        self._tiles: dict[Tuple[int, int], QGraphicsRectItem] = {}
        self._last_hash = None

    def update_heatmap(self, points: Iterable[Tuple[float, float]]) -> None:
        pts = list(points)
        if not pts:
            for item in self._tiles.values():
                item.setVisible(False)
            return
        new_hash = hash(tuple((round(p[0], 2), round(p[1], 2)) for p in pts))
        if new_hash == self._last_hash:
            return
        self._last_hash = new_hash
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        bins_x = max(1, int((max_x - min_x) / self._tile_size) + 1)
        bins_y = max(1, int((max_y - min_y) / self._tile_size) + 1)

        import numpy as np
        hist, xedges, yedges = np.histogram2d(xs, ys, bins=[bins_x, bins_y])
        kernel = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=float)
        kernel /= kernel.sum()
        padded = np.pad(hist, 1, mode="edge")
        smoothed = np.zeros_like(hist, dtype=float)
        for i in range(hist.shape[0]):
            for j in range(hist.shape[1]):
                window = padded[i:i+3, j:j+3]
                smoothed[i, j] = float((window * kernel).sum())

        epsilon = 1e-6
        log_vals = np.log(smoothed + epsilon)
        max_val = log_vals.max() if log_vals.size else 0.0
        min_val = log_vals.min() if log_vals.size else 0.0
        span = max(max_val - min_val, epsilon)
        threshold = 0.0
        for item in self._tiles.values():
            item.setVisible(False)

        for i in range(smoothed.shape[0]):
            for j in range(smoothed.shape[1]):
                val = log_vals[i, j]
                if val <= threshold:
                    continue
                x = xedges[i]
                y = yedges[j]
                key = (i, j)
                item = self._tiles.get(key)
                if item is None:
                    item = QGraphicsRectItem()
                    item.setZValue(1)
                    self._scene.addItem(item)
                    self._tiles[key] = item
                color = QColor("#00ff41")
                ratio = (val - min_val) / span
                if ratio > 0.66:
                    color = QColor("#ff3333")
                elif ratio > 0.33:
                    color = QColor("#ffcc33")
                else:
                    color = QColor("#00ff41")
                color.setAlpha(120)
                item.setRect(x, y, self._tile_size, self._tile_size)
                item.setBrush(QBrush(color))
                item.setPen(QPen(Qt.NoPen))
                item.setVisible(True)

class TacticalMapWidget(QGraphicsView):
    """QGraphicsView-based tactical map widget."""

    def _make_text_item(self, text: str, x: float, y: float, color: QColor, font: QFont | None = None) -> QGraphicsTextItem:
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(color)
        if font:
            item.setFont(font)
        item.setTransform(QTransform().scale(1.0, -1.0))  # Counter-flip for ENU Y-up
        item.setPos(x, y)
        self.scene.addItem(item)
        return item

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setBackgroundBrush(QBrush(Qt.black))
        self.setFocusPolicy(Qt.StrongFocus)

        # Scene is 2000m x 2000m with origin at center.
        self.scene.setSceneRect(-1000.0, -1000.0, 2000.0, 2000.0)
        self.setSceneRect(-1000.0, -1000.0, 2000.0, 2000.0)
        self._base_scene_rect = self.scene.sceneRect()

        # COORDINATE FRAME CONTRACT
        # World/Physics frame: ENU (X=East, Y=North, Z=Up) in meters
        # Scene frame: ENU-aligned (X=right=East, Y=up=North)
        # Y-axis flip applied ONCE at widget init via MapTransform.apply_to_view()
        # Mouse input handled in mousePressEvent with mapToScene (same orientation)
        # NO per-frame conversion elsewhere

        self._transform = MapTransform(pixels_per_meter=1.4)
        self._panning = False
        self._last_pan_pos = None
        self.follow_uav = True
        self.focus_mode = False
        self.focus_radius = 300.0

        # All items are persistent scene objects created once at init.
        self._grid_items: List[QGraphicsLineItem] = []
        self._update_grid()
        self._initial_center_done = False

        self.target_marker = TargetMarker()
        self.scene.addItem(self.target_marker)
        self.uav_marker = UAVMarker()
        self.scene.addItem(self.uav_marker)
        self._uav_tail = QGraphicsLineItem(0.0, 0.0, -30.0, 0.0)
        self._uav_tail.setPen(QPen(QColor("#00ffff"), 2))
        self._uav_tail.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self._uav_tail.setTransformOriginPoint(0.0, 0.0)
        self.scene.addItem(self._uav_tail)
        self.uav_marker.setZValue(7)
        self._uav_tail.setZValue(6.5)

        self._cep_label = self._make_text_item("", 0, 0, QColor("#00AA44"), QFont("Consolas", 9))
        self._cep_label.setZValue(50)

        self.impact_layer = ImpactEllipseLayer(self.scene)
        self.corridor_layer = CorridorLayer(self.scene)
        self.guidance_arrow = GuidanceArrow(self.scene)
        self.scatter_layer = ImpactScatterLayer(self.scene)
        self.trail_layer = UAVTrailLayer(self.scene)
        self.wind_field = WindFieldLayer(self.scene)
        self.heatmap_layer = ImpactHeatmapLayer(self.scene)
        self.boundary_indicator = BoundaryIndicator(self.scene)
        self.drift_arrow = DriftArrow(self.scene)

        self.show_scatter = True
        self.show_corridor_centerline = True

        self._last_uav_scene_pos: Tuple[float, float] | None = None
        self._last_target_scene_pos: Tuple[float, float] | None = None
        self._corridor_collapsed = False

        self._banner_item = self._make_text_item("", 0, 0, QColor("#ffaa00"), QFont("Consolas", 14))
        banner_font = QFont("Consolas", 14)
        banner_font.setBold(True)
        self._banner_item.setFont(banner_font)
        self._banner_item.setZValue(100)
        self._banner_flash_on = True
        self._banner_timer = None

        self._scale_bar_line = QGraphicsLineItem()
        self._scale_bar_tick_a = QGraphicsLineItem()
        self._scale_bar_tick_b = QGraphicsLineItem()
        self._scale_bar_text = self._make_text_item("0 ----- 50m", 0, 0, QColor("#00ff41"), QFont("Consolas", 9))
        # _scale_bar_text already added to scene by _make_text_item — set zValue only.
        self._scale_bar_text.setZValue(100)
        for item in (self._scale_bar_line, self._scale_bar_tick_a, self._scale_bar_tick_b):
            item.setZValue(100)
            self.scene.addItem(item)
        self._update_scale_bar()

        self._wind_warning = self._make_text_item("", 0, 0, QColor("#ffaa00"), QFont("Consolas", 9))
        self._wind_warning.setZValue(100)

        self._tdrop_label = self._make_text_item("", 0, 0, QColor("#ffffff"), QFont("Consolas", 9))
        self._tdrop_label.setZValue(100)

        self._phit_label = self._make_text_item("", 0, 0, QColor("#00ff41"), QFont("Consolas", 9))
        self._phit_label.setZValue(100)

        self._transform.apply_to_view(self)
        self.centerOn(0.0, 0.0)

        self._validate_startup()

        self._camera_feed_layer = CameraFeedLayer(self.viewport())
        self._reposition_camera_feed()
        self._camera_feed_layer.show()

        self._wind_indicator = WindIndicatorLayer(self.viewport())
        self._reposition_wind_indicator()

        # Ensure camera feed sits below other viewport children.
        self._camera_feed_layer.lower()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_center_done:
            self._initial_center_done = True
            self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
            current_scale = abs(self.transform().m11())
            if current_scale > 0:
                self._transform.pixels_per_meter = current_scale
            self._update_grid()

    def _update_grid(self) -> None:
        for item in self._grid_items:
            self.scene.removeItem(item)
        self._grid_items.clear()

        ppm = self._transform.pixels_per_meter
        if ppm < 0.3:
            spacing = 1000
        elif ppm < 0.8:
            spacing = 500
        elif ppm < 2.0:
            spacing = 200
        elif ppm < 5.0:
            spacing = 100
        elif ppm < 15.0:
            spacing = 50
        else:
            spacing = 10
        major_every = spacing * 5

        minor_pen = QPen(QColor(40, 40, 40), 0)
        major_pen = QPen(QColor(70, 70, 70), 1)
        minor_pen.setCosmetic(True)
        major_pen.setCosmetic(True)

        extent = 2000
        for x in range(-extent, extent + spacing, spacing):
            pen = major_pen if (x % major_every == 0) else minor_pen
            line = self.scene.addLine(x, -extent, x, extent, pen)
            line.setZValue(0)
            self._grid_items.append(line)

        for y in range(-extent, extent + spacing, spacing):
            pen = major_pen if (y % major_every == 0) else minor_pen
            line = self.scene.addLine(-extent, y, extent, y, pen)
            line.setZValue(0)
            self._grid_items.append(line)

    def resizeEvent(self, event) -> None:
        self._reposition_camera_feed()
        self._reposition_wind_indicator()
        super().resizeEvent(event)
        self._update_grid()

    def _reposition_camera_feed(self):
        if hasattr(self, '_camera_feed_layer'):
            viewport = self.viewport()
            self._camera_feed_layer.setGeometry(
                0, 0, viewport.width(), viewport.height()
            )

    def _reposition_wind_indicator(self):
        if hasattr(self, '_wind_indicator'):
            viewport = self.viewport()
            w = self._wind_indicator.width()
            h = self._wind_indicator.height()
            x = 20
            y = viewport.height() - h - 20
            self._wind_indicator.move(x, y)


    # ---- Public update methods (no item creation) ----

    def update_vehicle_position(self, x: float, y: float, heading: float) -> None:
        # Persistent items: update only geometry.
        sx, sy = self._transform.world_to_scene(x, y)
        self.uav_marker.set_position(sx, sy)
        self.uav_marker.set_heading(heading)
        self._uav_tail.setPos(sx, sy)
        self._uav_tail.setRotation(float(heading))
        self._last_uav_scene_pos = (sx, sy)
        self.trail_layer.update(sx, sy)
        if self.follow_uav:
            self.centerOn(sx, sy)
        if self.focus_mode:
            self.boundary_indicator.update(QPointF(sx, sy), self.sceneRect())

    def update_target(self, x: float, y: float) -> None:
        # Persistent items: update only geometry.
        sx, sy = self._transform.world_to_scene(x, y)
        self.target_marker.set_position(sx, sy)
        self._last_target_scene_pos = (sx, sy)
        if self.focus_mode:
            r = self.focus_radius
            self.setSceneRect(sx - r, sy - r, r * 2.0, r * 2.0)
            self.centerOn(sx, sy)
            self._update_scale_bar()
        elif not self._initial_center_done and not self.follow_uav:
            self.centerOn(sx, sy)
            self._initial_center_done = True

    def update_impact_ellipse(self, mean_x: float, mean_y: float, a: float, b: float, angle: float) -> None:
        # Persistent items: update only geometry.
        sx, sy = self._transform.world_to_scene(mean_x, mean_y)
        sa = float(a) * self._transform.pixels_per_meter
        sb = float(b) * self._transform.pixels_per_meter
        self.impact_layer.update(sx, sy, sa, sb, angle)
        self._last_ellipse_center_scene = (float(sx), float(sy))

    def update_corridor(self, polygon_points: Iterable[Tuple[float, float]]) -> None:
        # Persistent items: update only geometry.
        scene_pts = [self._transform.world_to_scene(x, y) for x, y in polygon_points]
        self.corridor_layer.update_corridor(scene_pts)
        self.corridor_layer.set_centerline_visible(self.show_corridor_centerline)
        self._corridor_collapsed = self.corridor_layer.is_collapsed()

    def update_camera_feed(self, image) -> None:
        # Raw overlay, no georeferencing — None triggers NO CAMERA overlay.
        if image is None:
            self._camera_feed_layer._pixmap = None
            self._camera_feed_layer._needs_redraw = True
            self._camera_feed_layer.update()
            return
        vp = self.viewport()
        scaled = image.scaled(
            vp.width(),
            vp.height(),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        self._camera_feed_layer.update_frame(scaled)

    def update_guidance_arrow(self) -> None:
        # Persistent items: update only geometry.
        if not self._last_uav_scene_pos:
            return
        line = self.corridor_layer.centerline_points()
        if not line:
            return
        u = QPointF(self._last_uav_scene_pos[0], self._last_uav_scene_pos[1])
        a, b = line
        vx = b.x() - a.x()
        vy = b.y() - a.y()
        denom = vx * vx + vy * vy
        if denom <= 0:
            return
        t = ((u.x() - a.x()) * vx + (u.y() - a.y()) * vy) / denom
        t = max(0.0, min(1.0, t))
        px = a.x() + vx * t
        py = a.y() + vy * t
        dx = px - u.x()
        dy = py - u.y()
        if math.hypot(dx, dy) < 1.0:
            self.guidance_arrow.set_visible(False)
            return
        self.guidance_arrow.set_visible(True)
        self.guidance_arrow.update(u.x(), u.y(), px, py)

    def update_wind_indicator(self, wind_x: float, wind_y: float) -> None:
        if hasattr(self, "_wind_indicator"):
            self._wind_indicator.update_wind(wind_x, wind_y)

    def update_wind(self, wind_x: float, wind_y: float) -> None:
        sx = float(wind_x) * self._transform.pixels_per_meter
        sy = float(wind_y) * self._transform.pixels_per_meter
        self.wind_field.update_field(sx, sy, self._transform.pixels_per_meter, self._last_target_scene_pos)

    def update_scatter(self, points: Iterable[Tuple[float, float]]) -> None:
        if not self.show_scatter:
            self.scatter_layer.update_scatter([])
            return
        scene_pts = [self._transform.world_to_scene(x, y) for x, y in points]
        self.scatter_layer.update_scatter(scene_pts)

    def update_heatmap(self, points: Iterable[Tuple[float, float]]) -> None:
        scene_pts = [self._transform.world_to_scene(x, y) for x, y in points]
        self.heatmap_layer.update_heatmap(scene_pts)

    # ---- Interaction ----

    def wheelEvent(self, event) -> None:  # noqa: N802
        modifiers = event.modifiers()
        angle_delta = event.angleDelta()
        pixel_delta = event.pixelDelta()

        # ZOOM: Only when Ctrl is held
        if modifiers & Qt.ControlModifier:
            delta = angle_delta.y()
            if delta == 0:
                return
            factor = 1.15 if delta > 0 else 1.0 / 1.15
            mouse_pos = event.position().toPoint()
            old_pos = self.mapToScene(mouse_pos)
            new_ppm = self._transform.pixels_per_meter * factor
            new_ppm = max(0.1, min(50.0, new_ppm))
            if abs(new_ppm - self._transform.pixels_per_meter) < 1e-9:
                return
            self._transform.pixels_per_meter = new_ppm
            self._transform.apply_to_view(self)
            new_pos = self.mapToScene(mouse_pos)
            delta_scene = new_pos - old_pos
            self.translate(delta_scene.x(), delta_scene.y())
            self._update_scale_bar()
            self._update_grid()
            event.accept()
            return

        # PAN: All scroll without Ctrl (trackpad or mouse)
        # Use pixelDelta if available (Linux/Mac trackpad)
        if not pixel_delta.isNull():
            dx = pixel_delta.x() / self._transform.pixels_per_meter
            dy = -pixel_delta.y() / self._transform.pixels_per_meter
            self.translate(dx, dy)
            event.accept()
            return

        # Use angleDelta for pan (Windows trackpad or mouse without Ctrl)
        dx = angle_delta.x() / self._transform.pixels_per_meter
        dy = -angle_delta.y() / self._transform.pixels_per_meter
        if abs(dx) > 0.001 or abs(dy) > 0.001:
            self.translate(dx, dy)
            event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            world_x = scene_pos.x()
            world_y = scene_pos.y()
            self.update_target(world_x, world_y)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning and self._last_pan_pos is not None:
            delta = event.pos() - self._last_pan_pos
            self._last_pan_pos = event.pos()
            self.translate(delta.x() * -1, delta.y() * -1)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self._last_pan_pos = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_F1:
            self.show_scatter = not self.show_scatter
            self.scatter_layer.set_visible(self.show_scatter)
            event.accept()
            return
        if event.key() == Qt.Key_F3:
            self.show_corridor_centerline = not self.show_corridor_centerline
            self.corridor_layer.set_centerline_visible(self.show_corridor_centerline)
            event.accept()
            return
        if event.key() == Qt.Key_F4:
            self.follow_uav = not self.follow_uav
            event.accept()
            return
        if event.key() == Qt.Key_F5:
            self.focus_mode = not self.focus_mode
            if not self.focus_mode:
                self.setSceneRect(self._base_scene_rect)
                self._update_scale_bar()
            event.accept()
            return
        super().keyPressEvent(event)

    def update_cep_label(self, cep_m: float) -> None:
        center = getattr(self, "_last_ellipse_center_scene", None)
        if center is None:
            self._cep_label.setPlainText("")
            return
        if float(cep_m) > 999.9:
            text = "CEP\u2085\u2080: ---"
        else:
            text = f"CEP\u2085\u2080: {float(cep_m):.1f} m"
        self._cep_label.setPlainText(text)
        sx, sy = center
        # Label 10 px below 68% ellipse center. View applies scale(pxm, -pxm),
        # so "below on screen" is negative Y in scene coords.
        self._cep_label.setPos(float(sx), float(sy) - 10.0)

    def update_status(self, status: str) -> None:
        if getattr(self, "_corridor_collapsed", False):
            self._banner_item.setDefaultTextColor(QColor("#ff3344"))
            self._banner_item.setPlainText("CORRIDOR COLLAPSED")
            rect = self.scene.sceneRect()
            br = self._banner_item.boundingRect()
            self._banner_item.setPos(rect.center().x() - br.width() * 0.5, rect.top() + 20.0)
            return
        text = (status or "").strip().upper()
        if not text:
            self._banner_item.setPlainText("")
            return
        color = QColor("#ffaa00")
        if text == "NO DROP":
            color = QColor("#ff3344")
        elif text == "APPROACH CORRIDOR":
            color = QColor("#ffaa00")
        elif text == "IN DROP ZONE":
            color = QColor("#00ff41")
        elif text == "DROP NOW":
            color = QColor("#44ff44")
        self._banner_item.setDefaultTextColor(color)
        self._banner_item.setPlainText(text)
        rect = self.scene.sceneRect()
        br = self._banner_item.boundingRect()
        self._banner_item.setPos(rect.center().x() - br.width() * 0.5, rect.top() + 20.0)

        if text == "DROP NOW":
            if self._banner_timer is None:
                from PySide6.QtCore import QTimer
                self._banner_timer = QTimer(self)
                self._banner_timer.setInterval(400)
                self._banner_timer.timeout.connect(self._toggle_banner_flash)
                self._banner_timer.start()
        else:
            if self._banner_timer is not None:
                self._banner_timer.stop()
                self._banner_timer = None
            self._banner_item.setVisible(True)

    def _toggle_banner_flash(self) -> None:
        self._banner_flash_on = not self._banner_flash_on
        self._banner_item.setVisible(self._banner_flash_on)

    def _update_scale_bar(self) -> None:
        length = 50.0 * self._transform.pixels_per_meter
        rect = self.scene.sceneRect()
        x = rect.left() + 20.0
        y = rect.bottom() + 20.0
        pen = QPen(QColor("#00ff41"), 2)
        self._scale_bar_line.setPen(pen)
        self._scale_bar_tick_a.setPen(pen)
        self._scale_bar_tick_b.setPen(pen)
        self._scale_bar_line.setLine(x, y, x + length, y)
        self._scale_bar_tick_a.setLine(x, y - 5.0, x, y + 5.0)
        self._scale_bar_tick_b.setLine(x + length, y - 5.0, x + length, y + 5.0)
        self._scale_bar_text.setDefaultTextColor(QColor("#00ff41"))
        self._scale_bar_text.setFont(QFont("Consolas", 9))
        self._scale_bar_text.setPlainText("0 ----- 50m")
        self._scale_bar_text.setPos(x, y + 8.0)

    def _validate_startup(self) -> None:
        if self.scene is None or self.scene.sceneRect().isNull():
            raise RuntimeError("TacticalMap initialization incomplete")
        if self._transform is None:
            raise RuntimeError("TacticalMap initialization incomplete")
        if self.uav_marker is None or self.target_marker is None:
            raise RuntimeError("TacticalMap initialization incomplete")

    def normalize_transform(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self._transform.apply_to_view(self)
        self.centerOn(center)

    def update_wind_warning(self, show: bool) -> None:
        if not show:
            self._wind_warning.setPlainText("")
            return
        self._wind_warning.setPlainText("WIND MODEL SIMPLIFIED")
        rect = self.scene.sceneRect()
        self._wind_warning.setPos(rect.left() + 20.0, rect.top() + 60.0)

    def update_release_timer(self, seconds: float | None) -> None:
        if seconds is None or seconds < 0:
            self._tdrop_label.setPlainText("")
            return
        color = QColor("#ffffff")
        if seconds < 3:
            color = QColor("#ff3333")
        elif seconds < 10:
            color = QColor("#ffcc33")
        self._tdrop_label.setDefaultTextColor(color)
        self._tdrop_label.setPlainText(f"T-DROP: {seconds:.0f} s")
        rect = self.scene.sceneRect()
        self._tdrop_label.setPos(rect.left() + 20.0, rect.top() + 80.0)

    def update_p_hit(self, p_hit: float | None, ci: float | None) -> None:
        if p_hit is None:
            self._phit_label.setPlainText("")
            return
        color = QColor("#ff3333")
        if p_hit > 0.7:
            color = QColor("#00ff41")
        elif p_hit > 0.4:
            color = QColor("#ffcc33")
        self._phit_label.setDefaultTextColor(color)
        if ci is not None:
            self._phit_label.setPlainText(f"P(HIT): {p_hit:.2f} ± {ci:.2f}")
        else:
            self._phit_label.setPlainText(f"P(HIT): {p_hit:.2f}")
        rect = self.scene.sceneRect()
        self._phit_label.setPos(rect.left() + 20.0, rect.top() + 100.0)

    def update_drift(self, release_x: float, release_y: float, impact_x: float, impact_y: float) -> None:
        sx1, sy1 = self._transform.world_to_scene(release_x, release_y)
        sx2, sy2 = self._transform.world_to_scene(impact_x, impact_y)
        self.drift_arrow.update(sx1, sy1, sx2, sy2)
        self.drift_arrow.set_visible(True)
