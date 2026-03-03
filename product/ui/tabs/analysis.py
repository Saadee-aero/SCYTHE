"""
Analysis tab. Read-only display of analysis metrics and sensitivity plots.
No sliders, no inputs, no recomputation. For understanding only.
"""

import matplotlib.patches as mpatches

from product.ui import plots

from product.ui.ui_theme import (
    BG_MAIN,
    BG_PANEL,
    BORDER_SUBTLE,
    ACCENT_GO,
    FONT_FAMILY,
    FONT_SIZE_H3,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_SMALL,
)


# ---------------------------------------------------------------------------
# Reusable block renderer — uniform formatting for all 6 left-column blocks
# ---------------------------------------------------------------------------

def _draw_block(ax, title, primary_label, primary_value, rows, empty_msg=None):
    """
    Draw a single metric block inside *ax* (an inset_axes).

    Parameters
    ----------
    ax : matplotlib Axes
    title : str            – Centered uppercase title
    primary_label : str    – Label for the primary (hero) metric
    primary_value : str    – Formatted value string for the hero metric
    rows : list[tuple]     – Up to 3 (label, value) secondary metric rows
    empty_msg : str|None   – If set, render only this message (no data state)
    """
    ax.set_axis_off()
    ax.set_facecolor(BG_PANEL)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Border — full extent so adjacent blocks touch (no gap)
    ax.add_patch(mpatches.Rectangle(
        (0, 0), 1, 1,
        linewidth=1, edgecolor=BORDER_SUBTLE, facecolor="none",
        transform=ax.transAxes,
    ))

    # Title — slightly dim green, 2px larger
    ax.text(
        0.5, 0.92, title, transform=ax.transAxes,
        fontsize=FONT_SIZE_H3 + 2, color="#33cc55",
        ha="center", va="top", family=FONT_FAMILY, weight="bold",
    )

    if empty_msg:
        ax.text(
            0.5, 0.45, empty_msg, transform=ax.transAxes,
            fontsize=FONT_SIZE_CAPTION, color=ACCENT_GO,
            ha="center", va="center", family=FONT_FAMILY,
        )
        return

    # Primary metric (slightly larger)
    ax.text(
        0.08, 0.72, primary_label, transform=ax.transAxes,
        fontsize=FONT_SIZE_BODY + 1, color=ACCENT_GO,
        ha="left", va="center", family=FONT_FAMILY,
    )
    ax.text(
        0.92, 0.72, primary_value, transform=ax.transAxes,
        fontsize=FONT_SIZE_BODY + 1, color=ACCENT_GO,
        ha="right", va="center", family=FONT_FAMILY,
    )

    # Secondary metrics
    y = 0.52
    dy = 0.18
    for label, value in rows[:3]:
        ax.text(
            0.08, y, label, transform=ax.transAxes,
            fontsize=FONT_SIZE_BODY, color=ACCENT_GO,
            ha="left", va="center", family=FONT_FAMILY,
        )
        ax.text(
            0.92, y, value, transform=ax.transAxes,
            fontsize=FONT_SIZE_BODY, color=ACCENT_GO,
            ha="right", va="center", family=FONT_FAMILY,
        )
        y -= dy


# ---------------------------------------------------------------------------
# Individual block builders — extract data from snapshot kwargs
# ---------------------------------------------------------------------------

def _block_cep(ax, cep50, hit_pct, ci_low, ci_high):
    if cep50 is None and hit_pct is None:
        _draw_block(ax, "CEP SUMMARY", "", "", [], empty_msg="No evaluation data")
        return
    primary_val = f"{float(hit_pct):.1f} %" if hit_pct is not None else "--"
    rows = []
    if cep50 is not None:
        rows.append(("CEP50", f"{float(cep50):.2f} m"))
    if ci_low is not None and ci_high is not None:
        rows.append(("95% CI", f"[{float(ci_low):.1f}, {float(ci_high):.1f}] %"))
    _draw_block(ax, "CEP SUMMARY", "HIT %", primary_val, rows)


def _block_fragility(ax, fragility_state, threshold_pct, p_hit):
    if not fragility_state or not isinstance(fragility_state, dict):
        _draw_block(ax, "FRAGILITY", "", "", [], empty_msg="No fragility data")
        return
    margin = fragility_state.get("margin_pct")
    zone = fragility_state.get("zone", "--")
    stability = fragility_state.get("advisory_stability", "--")
    primary_val = f"{float(margin):+.1f} %" if margin is not None else "--"
    rows = [
        ("Zone", str(zone)),
        ("Advisory Stability", str(stability)),
    ]
    _draw_block(ax, "FRAGILITY", "MARGIN TO THRESHOLD", primary_val, rows)


def _block_corridor(ax, release_corridor_matrix):
    if not release_corridor_matrix or not isinstance(release_corridor_matrix, dict):
        _draw_block(ax, "RELEASE CORRIDOR", "", "", [], empty_msg="No corridor data")
        return
    w = release_corridor_matrix.get("corridor_width_m")
    mn = release_corridor_matrix.get("min_offset_m")
    mx = release_corridor_matrix.get("max_offset_m")
    primary_val = (f"{float(w):.1f} m" if w is not None else "--")
    rows = []
    if mn is not None:
        rows.append(("Left Bound", f"{float(mn):.0f} m"))
    if mx is not None:
        rows.append(("Right Bound", f"+{float(mx):.0f} m"))
    _draw_block(ax, "RELEASE CORRIDOR", "VALID WINDOW WIDTH", primary_val, rows)


def _block_sensitivity(ax, sensitivity_matrix, dominant_risk_factor):
    if not sensitivity_matrix or not isinstance(sensitivity_matrix, dict):
        _draw_block(ax, "SENSITIVITY", "", "", [], empty_msg="No sensitivity data")
        return
    param_labels = {
        "wind": "Wind",
        "altitude": "Altitude",
        "velocity": "Velocity",
    }
    dominant_label = param_labels.get(dominant_risk_factor, str(dominant_risk_factor or "--"))
    sorted_params = sorted(
        [(k, abs(float(v))) for k, v in sensitivity_matrix.items() if k in param_labels],
        key=lambda t: t[1],
        reverse=True,
    )
    rows = [(param_labels[k], f"{mag:.4f}") for k, mag in sorted_params[:3]]
    _draw_block(ax, "SENSITIVITY", "DOMINANT PARAMETER", dominant_label, rows)


def _block_uncertainty(ax, uncertainty_contribution):
    if not uncertainty_contribution or not isinstance(uncertainty_contribution, dict):
        _draw_block(ax, "UNCERTAINTY CONTRIBUTION", "", "", [], empty_msg="No contribution data")
        return
    param_labels = {
        "wind": "Wind",
        "release": "Release Timing",
        "velocity": "Velocity",
        "altitude": "Altitude",
    }
    sorted_items = sorted(
        [(k, float(v or 0)) for k, v in uncertainty_contribution.items() if k in param_labels],
        key=lambda t: t[1],
        reverse=True,
    )
    primary_label_str = param_labels.get(sorted_items[0][0], "--") if sorted_items else "--"
    rows = [(param_labels[k], f"{pct * 100:.0f} %") for k, pct in sorted_items[:3]]
    _draw_block(ax, "UNCERTAINTY CONTRIBUTION", "PRIMARY CONTRIBUTOR", primary_label_str, rows)


def _block_topology(ax, topology_matrix):
    if not topology_matrix or not isinstance(topology_matrix, dict):
        _draw_block(ax, "TOPOLOGY", "", "", [], empty_msg="No topology data")
        return
    cluster_count = topology_matrix.get("cluster_count")
    primary_size = topology_matrix.get("primary_cluster_size")
    secondary_size = topology_matrix.get("secondary_cluster_size")
    ecc = topology_matrix.get("eccentricity_ratio")
    cls = topology_matrix.get("dispersion_classification", "--")

    if cluster_count is not None:
        primary_val = str(int(cluster_count))
    elif ecc is not None:
        primary_val = f"{float(ecc):.3f}"
    else:
        primary_val = "--"

    primary_label = "CLUSTER COUNT" if cluster_count is not None else "ECCENTRICITY"
    rows = []
    if primary_size is not None:
        rows.append(("Primary Cluster", str(int(primary_size))))
    if secondary_size is not None:
        rows.append(("Secondary Cluster", str(int(secondary_size))))
    if cluster_count is None and ecc is not None:
        rows.append(("Eccentricity", f"{float(ecc):.3f}"))
    if cls and cls != "--":
        rows.append(("Classification", str(cls)))
    _draw_block(ax, "TOPOLOGY", primary_label, primary_val, rows)


# ---------------------------------------------------------------------------
# Graph placeholder
# ---------------------------------------------------------------------------

def _placeholder_graph(ax, title):
    """Subdued placeholder when precomputed curve is not provided."""
    ax.set_facecolor(BG_PANEL)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    ax.add_patch(mpatches.Rectangle(
        (0, 0), 1, 1,
        linewidth=1, edgecolor=BORDER_SUBTLE, facecolor="none",
            transform=ax.transAxes,
    ))
    ax.text(
        0.5, 0.55, title, transform=ax.transAxes,
        fontsize=FONT_SIZE_H3, color=ACCENT_GO,
        ha="center", va="center", family=FONT_FAMILY,
    )
    ax.text(
        0.5, 0.40,
        "Sensitivity sweep not performed.\nUse Opportunity Analysis to generate sweep.",
        transform=ax.transAxes,
        fontsize=FONT_SIZE_CAPTION, color=ACCENT_GO,
        ha="center", va="center", family=FONT_FAMILY,
    )


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    ax,
    impact_points=None,
    target_position=None,
    target_radius=None,
    uav_position=None,
    wind_mean=None,
    cep50=None,
    target_hit_percentage=None,
    prob_vs_distance=None,
    prob_vs_wind_uncertainty=None,
    impact_velocity_stats=None,
    max_safe_impact_speed=None,
    sensitivity_matrix=None,
    dominant_risk_factor=None,
    topology_matrix=None,
    release_corridor_matrix=None,
    fragility_state=None,
    uncertainty_contribution=None,
    dispersion_mode="standard",
    view_zoom=1.0,
    snapshot_timestamp=None,
    random_seed=None,
    n_samples=None,
    ci_low=None,
    ci_high=None,
    threshold_pct=None,
    p_hit=None,
    **_,
):
    """
    Draw Analysis tab.  All arguments optional.  Read-only; no recomputation.

    Layout: left column (40%) with 6 metric blocks, right column (60%) with
    2 probability graphs stacked vertically.
    """
    ax.set_axis_off()
    ax.set_facecolor(BG_MAIN)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # ------------------------------------------------------------------
    # Column geometry — small gap between stats and graphs
    # ------------------------------------------------------------------
    margin = 0
    col_gap = 0.02  # Gap between stats section and graphs section
    left_w = 0.44  # Wider blocks (rectangles) for clearer data display
    right_w = 1.0 - left_w - col_gap
    left_x = 0
    right_x = left_w + col_gap

    # ------------------------------------------------------------------
    # LEFT AREA — 6 blocks in 3 rows × 2 columns, touching
    # ------------------------------------------------------------------
    n_rows, n_cols = 3, 2
    row_gap = 0
    block_col_gap = 0
    usable_h = 1.0
    usable_w = left_w
    block_h = (usable_h - row_gap * (n_rows - 1)) / n_rows
    block_w = (usable_w - block_col_gap) / n_cols

    block_funcs = [
        lambda a: _block_cep(a, cep50, target_hit_percentage, ci_low, ci_high),
        lambda a: _block_fragility(a, fragility_state, threshold_pct, p_hit),
        lambda a: _block_corridor(a, release_corridor_matrix),
        lambda a: _block_sensitivity(a, sensitivity_matrix, dominant_risk_factor),
        lambda a: _block_uncertainty(a, uncertainty_contribution),
        lambda a: _block_topology(a, topology_matrix),
    ]

    for i, func in enumerate(block_funcs):
        row, col = i // n_cols, i % n_cols
        x = left_x + col * (block_w + block_col_gap)
        y_top = 1.0 - margin - row * (block_h + row_gap)
        rect = [x, y_top - block_h, block_w, block_h]
        block_ax = ax.inset_axes(rect)
        block_ax.set_clip_on(True)
        func(block_ax)

    # ------------------------------------------------------------------
    # RIGHT COLUMN — 2 graphs (top 60 %, bottom 40 %), boundaries touching
    # ------------------------------------------------------------------
    graph_total_h = 1.0
    top_h = 0.60
    bot_h = 0.40
    top_y = 1.0 - top_h
    bot_y = 0.0

    # Top graph: P(HIT) vs TARGET DISTANCE
    ax_top = ax.inset_axes([right_x, top_y, right_w, top_h])
    ax_top.set_clip_on(True)
    if prob_vs_distance is not None and len(prob_vs_distance) == 2:
        x_vals, y_vals = prob_vs_distance[0], prob_vs_distance[1]
        if len(x_vals) and len(y_vals):
            plots.plot_sensitivity(
                ax_top, x_vals, y_vals,
                "Target distance (m)", "Hit probability",
                title="P(HIT) vs TARGET DISTANCE",
            )
            ax_top.margins(x=0.01, y=0.01)
            ax_top.set_ylim(0, 1)
        else:
            _placeholder_graph(ax_top, "P(HIT) vs TARGET DISTANCE")
    else:
        _placeholder_graph(ax_top, "P(HIT) vs TARGET DISTANCE")

    # Bottom graph: P(HIT) vs WIND UNCERTAINTY
    ax_bot = ax.inset_axes([right_x, bot_y, right_w, bot_h])
    ax_bot.set_clip_on(True)
    if prob_vs_wind_uncertainty is not None and len(prob_vs_wind_uncertainty) == 2:
        x_vals, y_vals = prob_vs_wind_uncertainty[0], prob_vs_wind_uncertainty[1]
        if len(x_vals) and len(y_vals):
            plots.plot_sensitivity(
                ax_bot, x_vals, y_vals,
                "Wind uncertainty (m/s)", "Hit probability",
                title="P(HIT) vs WIND UNCERTAINTY",
            )
            ax_bot.margins(x=0.01, y=0.01)
            ax_bot.set_ylim(0, 1)
        else:
            _placeholder_graph(ax_bot, "P(HIT) vs WIND UNCERTAINTY")
    else:
        _placeholder_graph(ax_bot, "P(HIT) vs WIND UNCERTAINTY")

    # Remove gap: hide shared spines so graphs touch at boundary
    ax_top.spines["bottom"].set_visible(False)
    ax_bot.spines["top"].set_visible(False)

