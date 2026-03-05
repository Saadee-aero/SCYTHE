"""
Propagation context abstraction for airdrop physics.

Encapsulates payload parameters, wind model, and atmosphere for trajectory propagation.

Context is immutable after construction.
Use with_wind() or subset() to derive new contexts.
"""
from __future__ import annotations

import numpy as np

from product.physics.atmosphere import density_exponential


def build_propagation_context(
    mass,
    Cd,
    area,
    wind_ref,
    shear,
    target_z,
    dt,
    *,
    wind_profiles=None,
    z_levels=None,
) -> "_PropagationContext":
    """Factory for propagation context. Single entry point for context creation."""
    return _PropagationContext(
        mass=mass,
        Cd=Cd,
        area=area,
        wind_ref=wind_ref,
        shear=shear,
        target_z=target_z,
        dt=dt,
        wind_profiles=wind_profiles,
        z_levels=z_levels,
    )


class _PropagationContext:
    __slots__ = (
        "mass", "Cd", "area",
        "wind_ref", "shear",
        "target_z", "dt",
        "wind_profiles", "z_levels",
        "_wind_fn", "_init_done",
    )

    def __init__(
        self,
        mass,
        Cd,
        area,
        wind_ref,
        shear,
        target_z,
        dt,
        *,
        wind_profiles=None,
        z_levels=None,
        _wind_fn=None,
        _skip_validation: bool = False,
    ):
        # NOTE:
        # object.__setattr__ is used to bypass immutability guard during initialization.
        object.__setattr__(self, "mass", mass)
        object.__setattr__(self, "Cd", Cd)
        object.__setattr__(self, "area", area)
        object.__setattr__(self, "wind_ref", np.asarray(wind_ref, dtype=float))
        # Shear must be a 3-element vector. It is NOT per-sample stochastic.
        shear_arr = np.zeros(3, dtype=float) if shear is None else np.asarray(shear, dtype=float).reshape(3)
        assert shear_arr.shape == (3,)
        object.__setattr__(self, "shear", shear_arr)
        object.__setattr__(self, "target_z", target_z)
        object.__setattr__(self, "dt", dt)
        object.__setattr__(self, "wind_profiles", wind_profiles)
        object.__setattr__(self, "z_levels", z_levels)
        object.__setattr__(self, "_wind_fn", _wind_fn)
        object.__setattr__(self, "_init_done", True)
        if __debug__ and not _skip_validation:
            self._validate_wind_equivalence()

    def __setattr__(self, name, value):
        if getattr(self, "_init_done", False):
            raise AttributeError(
                "Context is immutable after construction. "
                "Use with_wind() or subset() to derive new contexts."
            )
        object.__setattr__(self, name, value)

    def with_wind(self, wind_ref, wind_profiles=None, z_levels=None):
        """Return a context with updated wind_ref and optional wind_profiles."""
        # Preserve vertical correlation data unless explicitly overridden.
        wind_prof = wind_profiles if wind_profiles is not None else self.wind_profiles
        zl = z_levels if z_levels is not None else self.z_levels
        return _PropagationContext(
            mass=self.mass,
            Cd=self.Cd,
            area=self.area,
            wind_ref=wind_ref,
            shear=self.shear,
            target_z=self.target_z,
            dt=self.dt,
            wind_profiles=wind_prof,
            z_levels=zl,
            _skip_validation=True,
        )

    def subset(self, mask):
        """Return a context with wind_ref and wind_profiles sliced by mask."""
        # IMPORTANT:
        # shear, dt, mass, Cd, area, target_z are global physics parameters
        # and must NOT be masked or altered per sample.
        assert self.shear.ndim == 1 and self.shear.shape[0] == 3
        return _PropagationContext(
            mass=self.mass,
            Cd=self.Cd,
            area=self.area,
            wind_ref=self.wind_ref[mask],
            shear=self.shear,
            target_z=self.target_z,
            dt=self.dt,
            wind_profiles=self.wind_profiles[mask] if self.wind_profiles is not None else None,
            z_levels=self.z_levels,
            _skip_validation=True,
        )

    def _wind_impl(self, z, wind_ref_sub, wind_profiles_sub):
        """Shared wind resolution. Uses same logic path as wind() and wind_for_mask."""
        if wind_profiles_sub is not None and self.z_levels is not None:
            from product.physics.wind_model import interpolate_wind_profiles
            return interpolate_wind_profiles(z, self.z_levels, wind_profiles_sub)
        w = np.asarray(wind_ref_sub, dtype=float)
        s = np.asarray(self.shear, dtype=float).reshape(3)
        if w.ndim == 1:
            w = np.broadcast_to(w.reshape(1, 3), (len(z), 3))
        return w + s[None, :] * z[:, None]

    def wind_for_mask(self, z, mask):
        """
        Wind at altitude(s) z for masked samples. Returns (M, 3). No new context created.

        IMPORTANT:
        wind_for_mask must remain mathematically identical to
        context.subset(mask).wind(z). Any change here must be mirrored in wind().
        """
        z = np.atleast_1d(np.asarray(z, dtype=float))
        assert z.ndim == 1
        if self._wind_fn is not None:
            w = self._wind_fn(z)
        else:
            w_ref_sub = self.wind_ref[mask]
            prof_sub = self.wind_profiles[mask] if self.wind_profiles is not None else None
            w = self._wind_impl(z, w_ref_sub, prof_sub)
        w = np.asarray(w, dtype=float)
        assert w.shape == (z.shape[0], 3)
        return w

    def _validate_wind_equivalence(self):
        """
        Internal debug-only check that wind_for_mask and subset(mask).wind
        remain mathematically identical. Runs once at context construction.
        """
        if self._wind_fn is not None:
            return
        wind_ref = np.asarray(self.wind_ref, dtype=float)
        N = wind_ref.shape[0]
        if N < 3:
            return
        # Small random sample in altitude and mask space
        rng = np.random.default_rng()
        # Altitudes between ground and a modest ceiling above target_z
        z_max = float(self.target_z) if float(self.target_z) > 0.0 else 100.0
        z_sample = rng.uniform(0.0, z_max, size=3)
        z_sample = np.atleast_1d(np.asarray(z_sample, dtype=float))
        # Random mask with at least one active sample
        mask = np.zeros(N, dtype=bool)
        k = min(5, N)
        idx = rng.choice(N, size=k, replace=False)
        mask[idx] = True
        w_mask = self.wind_for_mask(z_sample, mask)
        sub = self.subset(mask)
        w_sub = sub.wind(z_sample)
        if not np.allclose(w_mask, w_sub, rtol=0, atol=1e-12):
            raise AssertionError("wind_for_mask must match subset(mask).wind(z)")

    def with_wind_fn(self, fn):
        """Return a context that uses fn(z) for wind instead of default model."""
        return _PropagationContext(
            self.mass, self.Cd, self.area,
            self.wind_ref, self.shear,
            self.target_z, self.dt,
            wind_profiles=self.wind_profiles,
            z_levels=self.z_levels,
            _wind_fn=fn,
            _skip_validation=True,
        )

    def wind(self, z):
        """Wind at altitude(s) z. Returns array of shape (N, 3)."""
        if self._wind_fn is not None:
            w = self._wind_fn(z)
            w = np.asarray(w, dtype=float)
            z_arr = np.atleast_1d(np.asarray(z, dtype=float))
            assert z_arr.ndim == 1
            assert w.shape == (z_arr.shape[0], 3)
            return w
        z = np.atleast_1d(np.asarray(z, dtype=float))
        assert z.ndim == 1
        w = self._wind_impl(z, self.wind_ref, self.wind_profiles)
        w = np.asarray(w, dtype=float)
        assert w.shape == (z.shape[0], 3)
        return w

    def density(self, z):
        """Air density at altitude(s) z. Returns array of shape (N,)."""
        z = np.atleast_1d(np.asarray(z, dtype=float))
        rho = np.atleast_1d(density_exponential(z))
        assert rho.shape == z.shape
        return rho


def _debug_validate_clone_immutability():
    """Internal sanity check: clone methods and immutability. Runs only when __debug__."""
    ctx = build_propagation_context(1.0, 0.5, 0.01, np.zeros(3), None, 0.0, 0.01)
    ctx2 = ctx.with_wind(np.ones((1, 3)))
    assert ctx2 is not ctx
    assert ctx2.mass == ctx.mass and ctx2.Cd == ctx.Cd and ctx2.area == ctx.area
    assert ctx2.target_z == ctx.target_z and ctx2.dt == ctx.dt
    try:
        ctx2.mass = 999
        raise AssertionError("Immutability violated: assignment after construction")
    except AttributeError:
        pass


if __debug__:
    _debug_validate_clone_immutability()
