"""
Mission Overview tab. Command-and-control: decision banner, key metrics, target view.
Receives precomputed data only; no computation.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from product.ui import plots

# Import unified military-grade theme
from product.ui.ui_theme import (
    BG_MAIN,
    BG_PANEL,
    TEXT_PRIMARY,
    TEXT_LABEL,
    ACCENT_GO,
    ACCENT_NO_GO,
    BORDER_SUBTLE,
)


def render(
    ax,
    decision,
    target_hit_percentage,
    cep50,
    threshold,
    mode,
    impact_points,
    target_position,
    target_radius,
    advisory_result=None,
    **kwargs
):
    ax.set_facecolor(BG_MAIN)

    # Render directly into the provided axes — no inset, no wasted space
    _draw_target_view(
        ax,
        impact_points,
        target_position,
        target_radius,
        cep50,
        target_hit_percentage,
        kwargs.get("release_point"),
        kwargs.get("wind_vector"),
        kwargs.get("dispersion_mode", "standard"),
        kwargs.get("view_zoom", 1.0),
    )



def _draw_banner_metrics_advisory(
    ax,
    decision,
    target_hit_percentage,
    cep50,
    threshold,
    mode,
    advisory,
    n_samples,
    confidence_index=None,
    random_seed=None,
    target_radius=None,
):
    ax.set_axis_off()
    ax.set_facecolor(BG_PANEL)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # Panel border
    ax.add_patch(
        mpatches.Rectangle(
            (0.01, 0.01),
            0.98,
            0.98,
            linewidth=1,
            edgecolor=BORDER_SUBTLE,
            facecolor=BG_PANEL,
            transform=ax.transAxes,
        )
    )

    is_drop = str(decision).upper() == "DROP"
    color = ACCENT_GO if is_drop else ACCENT_NO_GO
    label = "DROP" if is_drop else "NO DROP"

    # 1. Decision Banner (Neutral bg, colored border)
    # y=0.78 to 0.96
    banner_box = mpatches.Rectangle(
        (0.05, 0.78),
        0.90,
        0.18,
        linewidth=2,
        edgecolor=color,
        facecolor=BG_PANEL,
        transform=ax.transAxes,
    )
    ax.add_patch(banner_box)

    ax.text(
        0.5,
        0.89,
        label,
        transform=ax.transAxes,
        fontsize=22,
        fontweight="bold",
        color=color,
        ha="center",
        va="center",
        family="monospace",
    )

    # Confidence index
    phit = float(target_hit_percentage) / 100.0
    ci = float(confidence_index) if confidence_index is not None else 0.5
    if ci >= 0.75:
        ci_label = "High"
    elif ci >= 0.50:
        ci_label = "Moderate"
    else:
        ci_label = "Low"

    ax.text(
        0.5,
        0.82,
        f"Confidence Index: {ci:.2f} ({ci_label})",
        transform=ax.transAxes,
        fontsize=9,
        color=TEXT_PRIMARY,
        ha="center",
        va="center",
        family="monospace",
        weight="bold",
    )

    # Stats line inside banner (Streamlit parity: THRESH)
    stats = (
        f"HIT {target_hit_percentage:.1f}% | THRESH {threshold:.1f}% | "
        f"CEP50 {cep50:.2f}m"
    )
    ax.text(
        0.5,
        0.74,
        stats,
        transform=ax.transAxes,
        fontsize=7.5,
        color=TEXT_LABEL,
        ha="center",
        va="top",
        family="monospace",
    )

    # 2. Key Metrics (aligned spacing)
    y = 0.62
    ax.text(
        0.06,
        y,
        "KEY METRICS",
        transform=ax.transAxes,
        fontsize=10,
        color=ACCENT_GO,
        family="monospace",
        va="center",
        weight="bold",
    )

    y -= 0.06

    try:
        samples_int = int(n_samples)
    except Exception:
        samples_int = 0
    hits_text = f"{int(samples_int * phit)}/{samples_int}" if samples_int > 0 else "--/--"

    def row(lbl, val, y_pos):
        ax.text(
            0.06,
            y_pos,
            lbl,
            transform=ax.transAxes,
            fontsize=9,
            color=TEXT_LABEL,
            va="center",
            ha="left",
            family="monospace",
        )
        ax.text(
            0.94,
            y_pos,
            val,
            transform=ax.transAxes,
            fontsize=9,
            color=TEXT_PRIMARY,
            ha="right",
            va="center",
            family="monospace",
        )

    row("Mode", str(mode), y)
    y -= 0.052
    row("Samples", str(samples_int) if samples_int > 0 else "--", y)
    y -= 0.052
    row("Seed", str(random_seed) if random_seed is not None else "--", y)
    y -= 0.052
    row("Hits", hits_text, y)
    y -= 0.052
    if target_radius is not None:
        row("Target Radius", f"{float(target_radius):.1f} m", y)

    # 3. Advisory Panel (aligned spacing)
    if advisory:
        y -= 0.12
        ax.text(
            0.06,
            y,
            "ADVISORY",
            transform=ax.transAxes,
            fontsize=10,
            color=ACCENT_GO,
            family="monospace",
            va="center",
            weight="bold",
        )
        y -= 0.06

        # Feasibility
        ax.text(
            0.06,
            y,
            "Feasibility",
            transform=ax.transAxes,
            fontsize=9,
            color=TEXT_LABEL,
            va="center",
            ha="left",
            family="monospace",
        )
        ax.text(
            0.94,
            y,
            advisory.current_feasibility,
            transform=ax.transAxes,
            fontsize=9,
            color=ACCENT_GO,
            ha="right",
            va="center",
            family="monospace",
        )
        y -= 0.052

        # Trend
        ax.text(
            0.06,
            y,
            "Trend",
            transform=ax.transAxes,
            fontsize=9,
            color=TEXT_LABEL,
            va="top",
            ha="left",
            family="monospace",
        )
        ax.text(
            0.94,
            y,
            advisory.trend_summary,
            transform=ax.transAxes,
            fontsize=8,
            color=TEXT_PRIMARY,
            ha="right",
            va="top",
            family="monospace",
            wrap=True,
        )
        y -= 0.052

        # Analytical Text
        def _to_analytic(direction):
            if not direction or direction == "Hold Position":
                return "Position optimal."
            mapping = {
                "Move Forward": "Gradient suggests +X region.",
                "Move Backward": "Feasibility improves in -X.",
                "Move Left": "Feasibility improves in -Y.",
                "Move Right": "Gradient suggests +Y region.",
                "Unsafe": "Env. uncertainty > limits.",
            }
            for k, v in mapping.items():
                if k in direction:
                    return v
            return f"Gradient shift: {direction}"

        analytic_msg = _to_analytic(advisory.suggested_direction)

        ax.text(
            0.06,
            y,
            "Analysis",
            transform=ax.transAxes,
            fontsize=9,
            color=TEXT_LABEL,
            va="top",
            ha="left",
            family="monospace",
        )
        ax.text(
            0.94,
            y,
            analytic_msg,
            transform=ax.transAxes,
            fontsize=8,
            color=TEXT_PRIMARY,
            ha="right",
            va="top",
            family="monospace",
            wrap=True,
        )


def _draw_target_view(
    ax,
    impact_points,
    target_position,
    target_radius,
    cep50,
    target_hit_percentage=None,
    release_point=None,
    wind_vector=None,
    dispersion_mode="standard",
    view_zoom=1.0,
):
    target_position = np.asarray(target_position, dtype=float).flatten()[:2]
    impact_points = np.asarray(impact_points, dtype=float)
    if impact_points.size == 0:
        impact_points = np.empty((0, 2), dtype=float)
    elif impact_points.ndim == 1:
        try:
            impact_points = impact_points.reshape(-1, 2)
        except Exception:
            impact_points = np.empty((0, 2), dtype=float)

    mode_val = str(dispersion_mode).strip().lower()
    if mode_val not in ("standard", "advanced"):
        mode_val = "standard"

    wind_speed = 0.0
    if wind_vector is not None:
        try:
            wind_speed = float(np.linalg.norm(np.asarray(wind_vector, dtype=float).reshape(2)))
        except Exception:
            wind_speed = 0.0

    p_hit_for_color = None
    if target_hit_percentage is not None:
        try:
            p_hit_val = float(target_hit_percentage)
            p_hit_for_color = p_hit_val / 100.0 if p_hit_val > 1.0 else p_hit_val
        except Exception:
            p_hit_for_color = None

    plots.plot_impact_dispersion(
        ax,
        impact_points,
        target_position,
        target_radius,
        cep50,
        release_point=release_point,
        wind_vector=wind_vector,
        mode=mode_val,
        P_hit=p_hit_for_color,
        wind_speed=wind_speed,
        show_density=(mode_val == "advanced"),
        view_zoom=view_zoom,
    )

    # Match Streamlit tab badge in top-left corner (aligned).
    mode_badge = "STANDARD DISPLAY" if mode_val == "standard" else "ADVANCED DISPLAY"
    dot_color = "#00FF66" if mode_val == "standard" else "#ffaa00"
    ax.add_patch(
        plt.Circle(
            (0.02, 0.98),
            0.008,
            transform=ax.transAxes,
            facecolor=dot_color,
            edgecolor="none",
            zorder=10,
        )
    )
    ax.text(
        0.042,
        0.98,
        f"IMPACT DISPERSION — {mode_badge}",
        transform=ax.transAxes,
        ha="left",
        va="top",
        color=TEXT_LABEL,
        fontsize=9,
        family="monospace",
        weight="bold",
        zorder=12,
    )

