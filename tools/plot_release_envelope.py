"""
Debugging visualization for AIRDROP-X release envelope.

Plots the offset–probability curve and optional heatmap P_hit(offset, time).
Does not modify any engine code.
"""

import sys
import io
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import matplotlib.pyplot as plt

from product.physics.propagation_context import build_propagation_context
from product.explorer import compute_release_envelope


def _suppress_prints():
    @contextlib.contextmanager
    def _quiet():
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            yield
        finally:
            sys.stdout = old
    return _quiet()


class _Config:
    def __init__(self):
        self.max_lateral_offset = 40.0
        self.offset_step = 4.0
        self.drop_probability_threshold = 0.5
        self.compute_heatmap = True
        self.max_release_time = 5.0
        self.release_time_step = 0.1
        self.target_radius = 15.0
        self.wind_sigma0 = 0.8
        self.wind_std = 0.8
        self.wind_sigma_altitude_coeff = 0.001
        self.wind_sigma_max = 4.0
        self.release_pos_sigma = 0.5
        self.velocity_sigma = 0.02
        self.enable_hybrid_estimation = False
        self.max_mc_verifications = 10
        self.random_seed = 42


def main():
    pos0 = np.array([0.0, 0.0, 300.0])
    vel0 = np.array([25.0, 0.0, 0.0])
    wind_mean = np.array([1.0, 0.0, 0.0])
    target_pos = np.array([72.0, 0.0, 0.0])

    context = build_propagation_context(
        mass=1.0, Cd=1.0, area=0.01,
        wind_ref=wind_mean, shear=None, target_z=0.0, dt=0.01,
    )
    config = _Config()

    with _suppress_prints():
        result = compute_release_envelope(context, config, pos0, vel0, target_pos)

    offsets = np.array([e.offset for e in result.envelope])
    optimal_p_hit = np.array([e.optimal_p_hit for e in result.envelope])
    smoothed_p_hit = np.array([e.smoothed_p_hit for e in result.envelope])

    # --- Plot 1: Offset probability curve ---
    fig1, ax1 = plt.subplots(figsize=(8, 5))

    for start, end in result.corridor_ranges:
        ax1.axvspan(start, end, alpha=0.2, color="green", label="_nolegend_")

    ax1.plot(offsets, optimal_p_hit, "b--", label="optimal_p_hit", linewidth=1.5)
    ax1.plot(offsets, smoothed_p_hit, "g-", label="smoothed_p_hit", linewidth=2)
    ax1.set_xlabel("Lateral offset (m)")
    ax1.set_ylabel("Hit probability")
    ax1.set_title("AIRDROP-X Release Envelope (Offset Probability Curve)")
    ax1.legend()
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(True, alpha=0.3)
    fig1.tight_layout()
    fig1.savefig(Path(__file__).parent / "release_envelope_curve.png", dpi=150)
    plt.show(block=False)

    # --- Plot 2: Heatmap (if available) ---
    if result.heatmap is not None and result.heatmap_offsets is not None and result.heatmap_times is not None:
        fig2, ax2 = plt.subplots(figsize=(10, 6))

        t_min = float(np.min(result.heatmap_times))
        t_max = float(np.max(result.heatmap_times))
        o_min = float(np.min(result.heatmap_offsets))
        o_max = float(np.max(result.heatmap_offsets))

        im = ax2.imshow(
            result.heatmap,
            aspect="auto",
            origin="lower",
            extent=[t_min, t_max, o_min, o_max],
            interpolation="bilinear",
        )
        cbar = fig2.colorbar(im, ax=ax2)
        cbar.set_label("P_hit")

        for entry in result.envelope:
            ax2.plot(entry.optimal_release_time, entry.offset, "r.", markersize=4)

        # Contour lines
        X = np.linspace(t_min, t_max, result.heatmap.shape[1])
        Y = np.linspace(o_min, o_max, result.heatmap.shape[0])
        levels = [0.5, 0.7, 0.9]
        cs = ax2.contour(X, Y, result.heatmap, levels=levels, colors="white", linewidths=1)
        ax2.clabel(cs, inline=True, fontsize=9, fmt={0.5: "P_hit = 0.5", 0.7: "P_hit = 0.7", 0.9: "P_hit = 0.9"})

        ax2.set_xlabel("Release time (s)")
        ax2.set_ylabel("Lateral offset (m)")
        ax2.set_title("AIRDROP-X Release Envelope Heatmap")
        fig2.tight_layout()
        fig2.savefig(Path(__file__).parent / "release_envelope_heatmap.png", dpi=150)
        plt.show()

    else:
        plt.show()


if __name__ == "__main__":
    main()
