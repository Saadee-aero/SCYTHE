"""Mock camera feed — Phase 3.1 placeholder.

Returns a solid dark-gray QImage sized to caller request. No real
hardware, no georeferencing. Silent fail: get_frame() returns None
if image cannot be produced.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QImage


class CameraFeed:
    def __init__(self) -> None:
        pass

    def get_frame(self, width: int = 800, height: int = 600) -> Optional[QImage]:
        try:
            w = max(1, int(width))
            h = max(1, int(height))
            img = QImage(w, h, QImage.Format_RGB32)
            img.fill(QColor(40, 40, 40))
            return img
        except Exception:
            return None
