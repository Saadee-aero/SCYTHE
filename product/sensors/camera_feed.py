"""Mock camera feed — Phase 3.1 placeholder.

Returns None until real camera connected. CameraFeedLayer handles
None by showing NO CAMERA overlay.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QImage


class CameraFeed:
    def __init__(self) -> None:
        pass

    def get_frame(self, width: int = 800, height: int = 600) -> Optional[QImage]:
        # Returns None until real camera connected.
        # CameraFeedLayer handles None by showing NO CAMERA overlay.
        return None
