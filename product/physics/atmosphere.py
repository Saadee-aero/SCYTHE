"""
Standard atmosphere model for altitude-dependent air density.

Exponential approximation suitable for low-altitude airdrop scenarios (0–3000 m).
"""

import numpy as np

# Sea-level standard density (kg/m^3)
RHO_0 = 1.225

# Atmospheric scale height (m)
SCALE_HEIGHT = 8500.0


def density_exponential(z: np.ndarray) -> np.ndarray:
    """
    Air density at altitude z (meters) using exponential atmosphere.

    rho(z) = rho0 * exp(-z / H)

    Vectorized over z. Altitudes below 0 are clamped to 0.

    Args:
        z: altitude array, any shape. SI meters.

    Returns:
        density array, same shape as z. SI kg/m^3.
    """
    z_safe = np.maximum(np.asarray(z, dtype=float), 0.0)
    return RHO_0 * np.exp(-z_safe / SCALE_HEIGHT)
