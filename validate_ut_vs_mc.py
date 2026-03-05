"""
Validation script: compare Monte Carlo and Unscented Transform impact estimators.

Runs both estimators on the same scenario and prints a side-by-side comparison
of mean impact point, mean error, and CEP50.

This script does not modify any engine code.
"""

import sys
import io
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from product.physics.propagation_context import build_propagation_context
from src.monte_carlo import run_monte_carlo
from product.uncertainty.unscented_propagation import propagate_unscented


def _suppress_prints():
    """Context manager to capture stdout (suppress MC timing prints)."""
    @contextlib.contextmanager
    def _quiet():
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            yield
        finally:
            sys.stdout = old
    return _quiet()


class _UTConfig:
    """Minimal config object carrying UT uncertainty parameters."""

    def __init__(
        self,
        wind_sigma0: float,
        wind_sigma_altitude_coeff: float,
        wind_sigma_max: float,
        release_pos_sigma: float,
        velocity_sigma: float,
    ):
        self.wind_sigma0 = wind_sigma0
        self.wind_sigma_altitude_coeff = wind_sigma_altitude_coeff
        self.wind_sigma_max = wind_sigma_max
        self.release_pos_sigma = release_pos_sigma
        self.velocity_sigma = velocity_sigma


def compute_cep_from_covariance(cov_2x2: np.ndarray) -> float:
    """
    CEP50 from a 2x2 Gaussian covariance matrix.

    Uses the Rayleigh approximation: CEP ≈ 1.1774 * sqrt(mean eigenvalue).
    """
    eigvals = np.linalg.eigvalsh(cov_2x2)
    eigvals = np.maximum(eigvals, 0.0)
    sigma_avg = float(np.sqrt(np.mean(eigvals)))
    return 1.1774 * sigma_avg


def run_comparison():
    """Run MC and UT on the same scenario and print comparison."""
    # --- Scenario parameters (matches mission_configs defaults) ---
    pos0 = np.array([0.0, 0.0, 100.0])
    vel0 = np.array([20.0, 0.0, 0.0])
    mass = 1.0
    Cd = 1.0
    area = 0.01
    wind_mean = np.array([2.0, 0.0, 0.0])
    wind_std = 0.8
    target_pos = np.array([72.0, 0.0, 0.0])
    dt = 0.01
    seed = 42
    n_mc = 1000

    context = build_propagation_context(
        mass=mass, Cd=Cd, area=area,
        wind_ref=wind_mean, shear=None, target_z=0.0, dt=dt,
    )

    # ------------------------------------------------------------------
    # 1) Monte Carlo (N=1000)
    # ------------------------------------------------------------------
    mc_config = {"n_samples": n_mc}
    with _suppress_prints():
        mc_impacts = run_monte_carlo(
            context, pos0, vel0, wind_std, mc_config, seed,
        )

    mc_impacts = np.asarray(mc_impacts, dtype=float).reshape(-1, 2)
    mc_mean = np.mean(mc_impacts, axis=0)
    mc_centered = mc_impacts - mc_mean[None, :]
    mc_cov = (mc_centered.T @ mc_centered) / float(mc_impacts.shape[0])

    # CEP relative to MC mean (dispersion around own mean, not target).
    mc_radial = np.linalg.norm(mc_impacts - mc_mean[None, :], axis=1)
    cep_mc = float(np.percentile(mc_radial, 50))

    # ------------------------------------------------------------------
    # 2) Unscented Transform
    # ------------------------------------------------------------------
    ut_config = _UTConfig(
        wind_sigma0=wind_std,
        wind_sigma_altitude_coeff=0.001,
        wind_sigma_max=4.0,
        release_pos_sigma=0.5,
        velocity_sigma=0.02,
    )

    with _suppress_prints():
        ut_mean, ut_cov = propagate_unscented(context, ut_config, pos0, vel0)

    cep_ut = compute_cep_from_covariance(ut_cov)

    # ------------------------------------------------------------------
    # 3) Comparison
    # ------------------------------------------------------------------
    mean_error = float(np.linalg.norm(ut_mean - mc_mean))

    print("=" * 60)
    print("ESTIMATOR COMPARISON: Monte Carlo vs Unscented Transform")
    print("=" * 60)
    print()
    print(f"Scenario:")
    print(f"  pos0       = {tuple(pos0)}")
    print(f"  vel0       = {tuple(vel0)}")
    print(f"  wind_mean  = {tuple(wind_mean)}")
    print(f"  wind_std   = {wind_std}")
    print(f"  target     = {tuple(target_pos[:2])}")
    print()
    print(f"Monte Carlo (N={n_mc}):")
    print(f"  MC mean    = ({mc_mean[0]:.4f}, {mc_mean[1]:.4f})")
    print(f"  MC cov diag= ({mc_cov[0,0]:.4f}, {mc_cov[1,1]:.4f})")
    print(f"  CEP_MC     = {cep_mc:.4f} m")
    print()
    print(f"Unscented Transform (2n+1 = 11 sigma points):")
    print(f"  UT mean    = ({ut_mean[0]:.4f}, {ut_mean[1]:.4f})")
    print(f"  UT cov diag= ({ut_cov[0,0]:.4f}, {ut_cov[1,1]:.4f})")
    print(f"  CEP_UT     = {cep_ut:.4f} m")
    print()
    print(f"Comparison:")
    print(f"  mean_error = {mean_error:.4f} m")
    print(f"  CEP diff   = {abs(cep_ut - cep_mc):.4f} m")
    print()

    if mean_error < 5.0:
        print("Result: PASS (mean error < 5 m)")
    else:
        print("Result: REVIEW (mean error >= 5 m — check UT config alignment)")
    print("=" * 60)


if __name__ == "__main__":
    run_comparison()
