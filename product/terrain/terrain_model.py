"""Terrain elevation model. Flat placeholder until DEM loader lands."""

from __future__ import annotations


class TerrainModel:
    def get_elevation(self, x_enu: float, y_enu: float) -> float:
        return 0.0

    def is_loaded(self) -> bool:
        return False
