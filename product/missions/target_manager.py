class Target:
    """
    Target definition for mission context.
    SI units: position (x, y, z) in m, radius (m).
    z = target elevation (ground level for impact).
    Backward compat: 2D (x, y) accepted; z defaults to 0.
    """

    def __init__(self, position, radius):
        self._position = None
        self._radius = None
        self.position = position
        self.radius = radius

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        v = [float(value[0]), float(value[1])]
        z = float(value[2]) if len(value) >= 3 else 0.0
        self._position = (v[0], v[1], z)

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        v = float(value)
        if v <= 0:
            raise ValueError("radius must be positive")
        self._radius = v
