"""
Reusable plotting functions. Military HUD style. No engine or advisory calls.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

_PANEL = "#0f120f"
_ACCENT = "#00ff41"
_ACCENT_DIM = "#1a4d1a"
_TARGET_RING = "#00ff41"
_CEP_RING = "#4a7c4a"
_SCATTER = "#00ff41"
_LABEL = "#6b8e6b"
_GRID = "#1a3a1a"
_GRID_DIM = "#0f200f"  # dimmer green for dispersion plot so target/ellipse dominate
_BORDER = "#2a3a2a"
_ELLIPSE_GREEN = "#00ff41"
_ELLIPSE_AMBER = "#ffaa00"
_ELLIPSE_RED = "#ff4444"


def get_probability_color(P_hit):
    """Single source of truth for P_hit → ellipse/legend color. No threshold logic elsewhere."""
    if P_hit is None:
        return "orange"
    if P_hit > 0.80:
        return _ELLIPSE_GREEN
    if P_hit >= 0.60:
        return _ELLIPSE_AMBER
    return _ELLIPSE_RED


def plot_impact_dispersion(
    ax,
    impact_points,
    target_position,
    target_radius,
    cep50=None,
    release_point=None,
    wind_vector=None,
    mode="standard",
    P_hit=None,
    wind_speed=None,
    show_density=False,
    view_zoom=1.0,
):
    """
    Render impact dispersion. mode='standard' (minimal) or 'advanced' (full diagnostics).
    No simulation change; rendering only.
    """
    impact_points = np.asarray(impact_points, dtype=float)
    target_position = np.asarray(target_position, dtype=float).flatten()[:2]
    r_target = float(target_radius) if target_radius is not None else 0.0
    r_cep = float(cep50) if (cep50 is not None and cep50 > 0) else 0.0
    is_standard = (mode == "standard")
    try:
        zoom = float(view_zoom)
    except Exception:
        zoom = 1.0
    if zoom <= 0:
        zoom = 1.0

    def _apply_zoom_limits():
        if zoom == 1.0:
            return
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        cx = 0.5 * (x0 + x1)
        cy = 0.5 * (y0 + y1)
        half_x = 0.5 * (x1 - x0) / zoom
        half_y = 0.5 * (y1 - y0) / zoom
        ax.set_xlim(cx - half_x, cx + half_x)
        ax.set_ylim(cy - half_y, cy + half_y)

    # Common: mean and covariance/ellipse
    if impact_points.size > 0:
        impact_x = impact_points[:, 0]
        impact_y = impact_points[:, 1]
        mean_x = float(np.mean(impact_x))
        mean_y = float(np.mean(impact_y))
        r = np.sqrt((impact_x - mean_x) ** 2 + (impact_y - mean_y) ** 2)
        max_dispersion = float(np.max(r))
    else:
        impact_x = impact_y = np.array([])
        mean_x, mean_y = float(target_position[0]), float(target_position[1])
        max_dispersion = 0.0

    mean_impact = np.mean(impact_points, axis=0) if impact_points.size > 0 else np.array(target_position, dtype=float)
    ellipse_width = ellipse_height = angle_deg = 0.0
    eigvals = eigvecs = None
    if impact_points.shape[0] >= 2:
        try:
            cov = np.cov(impact_points.T)
            eigvals, eigvecs = np.linalg.eigh(cov)
            order = eigvals.argsort()[::-1]
            eigvals = eigvals[order]
            eigvecs = eigvecs[:, order]
            semi_major_2sigma = 2.0 * np.sqrt(max(eigvals[0], 0.0))
            semi_minor_2sigma = 2.0 * np.sqrt(max(eigvals[1], 0.0))
            ellipse_width = 2.0 * semi_major_2sigma
            ellipse_height = 2.0 * semi_minor_2sigma
            angle_deg = np.degrees(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))
        except Exception:
            pass

    ax.set_facecolor(_PANEL)
    ax.tick_params(colors=_LABEL)
    ax.xaxis.label.set_color(_LABEL)
    ax.yaxis.label.set_color(_LABEL)
    for spine in ax.spines.values():
        spine.set_color(_BORDER)

    # ----- Standard mode: minimal layers only -----
    if is_standard:
        view_radius = max(3.0 * ellipse_width, 2.0 * r_target)
        view_radius = max(view_radius, 10.0)
        xmin, xmax = target_position[0] - view_radius, target_position[0] + view_radius
        ymin, ymax = target_position[1] - view_radius, target_position[1] + view_radius

        # Only draw circles/target/ellipse when simulation has run (impact_points present)
        if impact_points.shape[0] > 0:
            # Strict z-order: target 6, ellipse 7, wind 8, mean+bias 9, text 10
            r_draw = r_target + 3.0  # 3 units bigger radius
            ax.add_patch(
                plt.Circle(
                    target_position,
                    r_draw,
                    facecolor="none",
                    edgecolor="lime",
                    linewidth=5.5,
                    clip_on=True,
                    zorder=6,
                )
            )
            ax.scatter(target_position[0], target_position[1], color="lime", s=43, zorder=6, edgecolors=_PANEL, linewidths=0.5, clip_on=True)

        ellipse_color = get_probability_color(P_hit)
        if impact_points.shape[0] > 0 and ellipse_width > 0 and ellipse_height > 0:
            from matplotlib.patches import Ellipse
            ax.add_patch(
                Ellipse(
                    xy=(float(mean_impact[0]), float(mean_impact[1])),
                    width=ellipse_width + 6.0,
                    height=ellipse_height + 6.0,
                    angle=angle_deg,
                    edgecolor=ellipse_color,
                    facecolor="none",
                    linewidth=1.5,
                    linestyle="--",
                    clip_on=True,
                    zorder=7,
                )
            )

        if impact_points.shape[0] > 0 and wind_vector is not None:
            wind = np.asarray(wind_vector, dtype=float).reshape(2)
            wind_mag = float(np.linalg.norm(wind))
            if wind_mag > 0:
                arrow_length = 0.4 * view_radius
                direction = wind / wind_mag
                vec = direction * arrow_length
                ax.arrow(
                    target_position[0], target_position[1],
                    vec[0], vec[1],
                    width=0.18 * (arrow_length / 10.0),
                    head_width=0.9 * (arrow_length / 10.0),
                    head_length=1.3 * (arrow_length / 10.0),
                    color="#e6b800",
                    length_includes_head=True,
                    clip_on=True,
                zorder=8,
            )

        if impact_points.shape[0] > 0 and release_point is not None:
            rp = np.asarray(release_point, dtype=float).reshape(2)
            ax.scatter(rp[0], rp[1], color="yellow", s=120, marker="^", clip_on=True, zorder=8)

        if impact_points.shape[0] > 0:
            dx = mean_impact[0] - target_position[0]
            dy = mean_impact[1] - target_position[1]
            offset = float(np.sqrt(dx * dx + dy * dy))
        else:
            offset = 0.0
        if impact_points.shape[0] > 0 and offset > 0.5:
            ax.arrow(
                target_position[0], target_position[1], dx, dy,
                width=0.15 * min(offset / 10.0, 1.0),
                head_width=0.8,
                head_length=0.6,
                color="white",
                length_includes_head=True,
                clip_on=True,
                zorder=9,
            )
        if impact_points.shape[0] > 0:
            ax.scatter(mean_impact[0], mean_impact[1], color="#ffffff", s=63, marker="x", linewidths=2, clip_on=True, zorder=9)

        if impact_points.shape[0] > 0 and offset > 0.5:
            mid_x = (target_position[0] + mean_impact[0]) / 2
            mid_y = (target_position[1] + mean_impact[1]) / 2
            # Place text slightly above bias arrow to avoid overlap
            label_offset = 0.04 * view_radius
            ax.text(
                mid_x,
                mid_y + label_offset,
                f"Offset: {offset:.2f} m",
                color="white",
                fontsize=7,
                family="monospace",
                ha="center",
                va="center",
                zorder=10,
                bbox=dict(boxstyle="round,pad=0.35", facecolor=(0.05, 0.07, 0.05, 0.88), edgecolor="none"),
            )

        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        _apply_zoom_limits()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("X (m)", labelpad=0)
        ax.set_ylabel("Y (m)", labelpad=0)
        ax.tick_params(axis="both", pad=2)
        ax.set_axisbelow(True)
        # Dark, visible, low-distraction grid for standard mode.
        ax.grid(
            True,
            color=_GRID,
            alpha=0.48,
            linestyle="-",
            linewidth=0.7,
            )
        # Minimal legend: only when simulation has run
        op_handles = [
            Line2D([0], [0], marker="o", color="none", markerfacecolor="lime", markeredgecolor="lime", markersize=4, label="Target"),
            Line2D([0], [0], marker="x", color="#ffffff", markersize=5, label="Mean Impact"),
            Line2D([0], [0], linestyle="--", color=ellipse_color, linewidth=1.5, label="2σ Ellipse"),
            Line2D([0], [0], color="#e6b800", linewidth=1.5, label="Wind Vector"),
            Line2D([0], [0], marker="^", color="none", markerfacecolor="yellow", markeredgecolor="yellow", markersize=5, label="Release Point"),
        ]
        if impact_points.shape[0] == 0:
            ax.text(0.5, 0.5, "No simulation data", transform=ax.transAxes,
                    ha="center", va="center", fontsize=12, color=_LABEL, family="monospace")
        else:
            leg = ax.legend(
            handles=op_handles,
            loc="lower left",
            fontsize=6,
            framealpha=0.6,
            facecolor=(0.06, 0.08, 0.06),
            edgecolor="none",
            labelcolor="#b0d0b0",
            handlelength=1.2,
            handletextpad=0.5,
            borderpad=0.4,
        )
            leg.get_frame().set_boxstyle("round,pad=0.2")
            leg.set_zorder(9)
        ax.text(
            0.98, 0.02,
            "Model: Low-subsonic, drag-dominated free fall",
            transform=ax.transAxes,
            ha="right", va="bottom",
            fontsize=6, color=_LABEL, family="monospace", zorder=10,
        )
        return

    # ----- Advanced mode: full layers (only when simulation has run) -----
    if impact_points.shape[0] == 0:
        # No simulation: axes + grid only
        view_radius = max(2.0 * r_target, 10.0)
        xmin, xmax = target_position[0] - view_radius, target_position[0] + view_radius
        ymin, ymax = target_position[1] - view_radius, target_position[1] + view_radius
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        _apply_zoom_limits()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("X (m)", labelpad=0)
        ax.set_ylabel("Y (m)", labelpad=0)
        ax.tick_params(axis="both", pad=2)
        ax.set_axisbelow(True)
        ax.grid(True, color=_GRID, alpha=0.48, linestyle="-", linewidth=0.7)
        ax.text(
            0.98, 0.02,
            "Model: Low-subsonic, drag-dominated free fall",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=6, color=_LABEL, family="monospace", zorder=10,
        )
        return

    plot_radius = max(1.5 * max_dispersion, 1.5 * r_target)
    plot_radius = max(plot_radius, 1.0)
    plot_radius *= 1.1
    xmin, xmax = mean_x - plot_radius, mean_x + plot_radius
    ymin, ymax = mean_y - plot_radius, mean_y + plot_radius

    # Advanced layering policy (background -> foreground):
    # density(1/2), impacts(3), target+cep(6), ellipse/axes(7), wind/release(8), mean(9), legend/text(10+).
    ax.scatter(
        impact_points[:, 0],
        impact_points[:, 1],
        color=_SCATTER,
        alpha=0.35,
        s=10,
        edgecolors="none",
        clip_on=True,
        zorder=3,
    )
    ax.scatter(mean_impact[0], mean_impact[1], color="#ffffff", s=60, marker="x", linewidths=2, clip_on=True, zorder=9)

    if release_point is not None:
        rp = np.asarray(release_point, dtype=float).reshape(2)
        ax.scatter(rp[0], rp[1], color="yellow", s=120, marker="^", clip_on=True, zorder=8)

    r_draw = r_target + 3.0  # 3 units bigger radius
    ax.add_patch(
        plt.Circle(
            target_position,
            r_draw,
            facecolor=(0, 1, 0, 0.05),
            edgecolor="lime",
            linewidth=4.5,
            clip_on=True,
            zorder=6,
        )
    )
    ax.scatter(target_position[0], target_position[1], color=_TARGET_RING, s=43, zorder=6, edgecolors=_PANEL, linewidths=0.5, clip_on=True)

    if r_cep > 0:
        ax.add_patch(
            plt.Circle(
                mean_impact,
                r_cep,
                color=_CEP_RING,
                fill=False,
                linestyle="--",
                linewidth=4.2,
                alpha=0.9,
                clip_on=True,
                zorder=6,
            )
        )

    if wind_vector is not None:
        wind = np.asarray(wind_vector, dtype=float).reshape(2)
        wind_mag = float(np.linalg.norm(wind))
        if wind_mag > 0:
            arrow_length = 0.4 * plot_radius
            direction = wind / wind_mag
            vec = direction * arrow_length
            ax.arrow(
                target_position[0], target_position[1],
                vec[0], vec[1],
                width=0.18 * (arrow_length / 10.0),
                head_width=0.9 * (arrow_length / 10.0),
                head_length=1.3 * (arrow_length / 10.0),
                color="#e6b800",
                length_includes_head=True,
                clip_on=True,
                zorder=8,
            )

    eng_ellipse_color = get_probability_color(P_hit)
    if impact_points.shape[0] >= 2 and eigvals is not None and eigvecs is not None:
        try:
            from matplotlib.patches import Ellipse
            ax.add_patch(
                Ellipse(
                    xy=(mean_x, mean_y),
                    width=ellipse_width,
                    height=ellipse_height,
                    angle=angle_deg,
                    edgecolor=eng_ellipse_color,
                    facecolor="none",
                    linewidth=1.0,
                    linestyle="--",
                    alpha=0.8,
                    zorder=7,
                )
            )
            # Keep only confidence ellipse in engineering mode.
            # Principal-axis overlays are intentionally omitted to avoid layer clutter.
        except Exception:
            pass

    if show_density and impact_points.shape[0] >= 30:
        try:
            from scipy.stats import gaussian_kde
            xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:200j]
            coords = np.vstack([impact_x, impact_y])
            kde = gaussian_kde(coords)
            zi = kde(np.vstack([xi.flatten(), yi.flatten()]))
            zi = zi.reshape(xi.shape)
            ax.imshow(
                np.rot90(zi),
                extent=[xmin, xmax, ymin, ymax],
                cmap="viridis",
                alpha=0.08,
                aspect="auto",
                origin="lower",
                zorder=1,
            )
            ax.contour(
                xi,
                yi,
                zi,
                levels=6,
                colors=_CEP_RING,
                linewidths=0.8,
                zorder=2,
            )
        except Exception:
            pass
    elif impact_points.shape[0] < 30 and impact_points.shape[0] >= 2:
        ax.text(0.02, 0.02, "Density map requires \u2265 30 samples.", transform=ax.transAxes, ha="left", va="bottom", fontsize=6, color=_LABEL, family="monospace")

    # Use same view_radius logic as operator mode for consistent grid/dispersion sizing
    view_radius = max(3.0 * ellipse_width, 2.0 * r_target) if ellipse_width > 0 else 2.0 * r_target
    view_radius = max(view_radius, 10.0)
    ax.set_xlim(target_position[0] - view_radius, target_position[0] + view_radius)
    ax.set_ylim(target_position[1] - view_radius, target_position[1] + view_radius)
    _apply_zoom_limits()
    ax.set_aspect("equal", adjustable="datalim")

    ax.set_xlabel("X (m)", labelpad=0)
    ax.set_ylabel("Y (m)", labelpad=0)
    ax.tick_params(axis="both", pad=2)
    ax.set_axisbelow(True)
    ax.grid(
        True,
        color=_GRID,
        alpha=0.48,
        linestyle="-",
        linewidth=0.7,
    )
    ax.text(
        0.98,
        0.02,
        "Model: Low-subsonic, drag-dominated free fall",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=6,
        color=_LABEL,
        family="monospace",
        zorder=10,
    )

    # Legend: dynamic ellipse color, dashed to match, dark semi-transparent, zorder 9
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=_SCATTER, markeredgecolor="none", markersize=6, label="Impacts"),
        Line2D([0], [0], marker="x", color="#ffffff", markersize=8, label="Mean"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="lime", markeredgecolor="lime", markersize=6, label="Target"),
        Line2D([0], [0], linestyle="--", color=_CEP_RING, linewidth=1.2, label="CEP50"),
        Line2D([0], [0], linestyle="--", color=eng_ellipse_color, linewidth=1.0, label="2\u03c3 Confidence Ellipse"),
        Line2D([0], [0], color="#e6b800", linewidth=2, label="Wind"),
    ]
    if release_point is not None:
        handles.append(
            Line2D([0], [0], marker="^", color="none", markerfacecolor="yellow", markeredgecolor="yellow", markersize=8, label="Release Point"),
        )
    if show_density and impact_points.shape[0] >= 30:
        handles.append(
            Line2D([0], [0], linestyle="-", color=_CEP_RING, linewidth=1.2, label="Probability Density"),
        )
    leg_eng = ax.legend(
        handles=handles,
        loc="lower left",
        frameon=True,
        fontsize=6,
        framealpha=0.55,
        facecolor=(0.06, 0.08, 0.06),
        edgecolor="none",
        labelcolor="#b0d0b0",
        handlelength=1.2,
        handletextpad=0.5,
        borderpad=0.4,
    )
    leg_eng.get_frame().set_boxstyle("round,pad=0.2")
    leg_eng.set_zorder(11)


def plot_sensitivity(ax, x_values, y_values, x_label, y_label, title=None):
    x_values = np.asarray(x_values, dtype=float)
    y_values = np.asarray(y_values, dtype=float)
    ax.set_facecolor(_PANEL)
    ax.tick_params(colors=_LABEL)
    ax.plot(x_values, y_values, color=_ACCENT, clip_on=True)
    ax.margins(x=0.04, y=0.04)
    ax.set_xlabel(x_label, color=_LABEL)
    ax.set_ylabel(y_label, color=_LABEL)
    ax.grid(True, color=_GRID, alpha=0.6)
    if title is not None:
        ax.set_title(title, color=_LABEL)
    for spine in ax.spines.values():
        spine.set_color(_BORDER)


def create_figure_axes(nrows=1, ncols=1, figsize=(6, 6)):
    fig, ax = plt.subplots(nrows, ncols, figsize=figsize)
    if nrows == 1 and ncols == 1:
        return fig, ax
    return fig, ax
