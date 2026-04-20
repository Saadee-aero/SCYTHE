"""
Release envelope solver using Unscented Transform-based release-time explorer.

For a grid of lateral offsets relative to the UAV's current trajectory,
this module computes the optimal release time and hit probability at each
offset using the existing release-time explorer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from product.explorer.release_time_explorer import find_release_window, ReleaseWindowResult


def _group_offsets_into_intervals(
    offsets: List[float],
    tolerance: float,
) -> List[Tuple[float, float]]:
    """Group sorted offsets into contiguous (start, end) intervals."""
    if not offsets:
        return []
    intervals: List[Tuple[float, float]] = []
    start = offsets[0]
    prev = offsets[0]
    for o in offsets[1:]:
        if o - prev > tolerance * 1.5:
            intervals.append((start, prev))
            start = o
        prev = o
    intervals.append((start, prev))
    return intervals


@dataclass
class ReleaseEnvelopeEntry:
    """Single lateral offset entry in the release envelope."""

    offset: float
    optimal_release_time: float
    optimal_p_hit: float
    smoothed_p_hit: float
    release_window: List[Tuple[float, float]]
    optimal_p_hit_ut: float = 0.0
    optimal_p_hit_mc: Optional[float] = None


@dataclass
class ReleaseEnvelopeResult:
    """Result of lateral release envelope computation."""

    envelope: List[ReleaseEnvelopeEntry]
    feasible_offsets: List[float]
    corridor_ranges: List[Tuple[float, float]]
    heatmap: Optional[np.ndarray] = None
    heatmap_offsets: Optional[np.ndarray] = None
    heatmap_times: Optional[np.ndarray] = None
    impact_mean: Optional[np.ndarray] = None
    impact_cov: Optional[np.ndarray] = None


def compute_release_envelope(
    context,
    config,
    pos0,
    vel0,
    target_pos,
    motion_predictor=None,
) -> ReleaseEnvelopeResult:
    """
    Compute a lateral release envelope by scanning offsets perpendicular to
    the UAV's velocity vector and solving the 1D release-time problem at
    each offset.

    Parameters
    ----------
    context :
        PropagationContext (immutable).
    config :
        Configuration object with at least:
            max_lateral_offset – maximum lateral distance (m).
            offset_step       – lateral grid spacing (m).
        Also must carry the parameters required by find_release_window.
    pos0 : array_like, shape (3,)
        Current UAV position.
    vel0 : array_like, shape (3,)
        Current UAV velocity.
    target_pos : array_like, shape (2,) or (3,)
        Target position (world frame).
    motion_predictor : optional
        If provided, passed through to find_release_window for kinematic extrapolation.

    Returns
    -------
    ReleaseEnvelopeResult
        envelope         – one entry per lateral offset (optimal_p_hit for reporting).
        feasible_offsets – offsets where smoothed_p_hit >= threshold.
        corridor_ranges  – contiguous (start, end) offset intervals.
    """
    pos0 = np.asarray(pos0, dtype=float).reshape(3)
    vel0 = np.asarray(vel0, dtype=float).reshape(3)

    offset_max = float(getattr(config, "max_lateral_offset", 40.0))
    offset_step = float(getattr(config, "offset_step", 4.0))
    threshold = float(getattr(config, "drop_probability_threshold", 0.5))
    compute_heatmap = bool(getattr(config, "compute_heatmap", False))

    # Symmetric lateral grid via integer indexing (ensures offset=0 included).
    num_steps = int(round(offset_max / offset_step))
    offsets = offset_step * np.arange(-num_steps, num_steps + 1)

    # Lateral direction in horizontal plane (perpendicular to forward).
    forward_xy = np.asarray(vel0[:2], dtype=float)
    norm_fwd = float(np.linalg.norm(forward_xy))
    if norm_fwd < 1e-6:
        raise ValueError("UAV velocity too small to define release direction")

    forward = forward_xy / norm_fwd
    lateral = np.array([-forward[1], forward[0]], dtype=float)

    raw_results: List[Tuple[float, float, float, List[Tuple[float, float]], float, Optional[float]]] = []
    window_results: List[ReleaseWindowResult] = []
    for offset in offsets:
        pos_offset = pos0.copy()
        pos_offset[:2] += lateral * float(offset)

        window_result: ReleaseWindowResult = find_release_window(
            context,
            config,
            pos_offset,
            vel0,
            target_pos,
            motion_predictor=motion_predictor,
            offset=float(offset),
        )

        window_results.append(window_result)
        raw_results.append((
            float(offset),
            window_result.optimal_release_time,
            window_result.optimal_p_hit,
            window_result.release_window,
            window_result.results_table if compute_heatmap else None,
            window_result.optimal_p_hit_ut,
            window_result.optimal_p_hit_mc,
        ))

    # Smoothed P_hit via moving average (window=3).
    window = 3
    p_hits = np.array([r[2] for r in raw_results], dtype=float)
    half = window // 2
    smoothed = np.zeros_like(p_hits)
    for i in range(len(p_hits)):
        lo = max(0, i - half)
        hi = min(len(p_hits), i + half + 1)
        smoothed[i] = float(np.mean(p_hits[lo:hi]))

    envelope: List[ReleaseEnvelopeEntry] = []
    for i, row in enumerate(raw_results):
        offset, opt_t, opt_p, rw = row[0], row[1], row[2], row[3]
        p_hit_ut = row[5] if len(row) > 5 else opt_p
        p_hit_mc = row[6] if len(row) > 6 else None
        entry = ReleaseEnvelopeEntry(
            offset=offset,
            optimal_release_time=opt_t,
            optimal_p_hit=opt_p,
            smoothed_p_hit=smoothed[i],
            release_window=rw,
            optimal_p_hit_ut=p_hit_ut,
            optimal_p_hit_mc=p_hit_mc,
        )
        envelope.append(entry)

    # Corridor detection using smoothed_p_hit (not optimal_p_hit).
    feasible_offsets = sorted(e.offset for e in envelope if e.smoothed_p_hit >= threshold)
    corridor_ranges = _group_offsets_into_intervals(feasible_offsets, offset_step)

    heatmap = None
    heatmap_offsets = None
    heatmap_times = None

    if compute_heatmap:
        all_times = set()
        for row in raw_results:
            rt = row[4]
            if rt is not None:
                for r in rt:
                    all_times.add(r.time)
        time_grid = np.array(sorted(all_times), dtype=float)
        offset_grid = np.array([row[0] for row in raw_results], dtype=float)

        hm = np.zeros((len(offset_grid), len(time_grid)), dtype=float)
        for i, row in enumerate(raw_results):
            rt = row[4]
            if rt is None or len(rt) == 0:
                continue
            times = np.array([r.time for r in rt], dtype=float)
            p_hits = np.array([r.p_hit for r in rt], dtype=float)
            hm[i, :] = np.interp(
                time_grid,
                times,
                p_hits,
                left=0.0,
                right=0.0,
            )
        heatmap = hm
        heatmap_offsets = offset_grid
        heatmap_times = time_grid

    # Extract UT covariance from the best feasible entry for ellipse rendering.
    best_impact_mean: Optional[np.ndarray] = None
    best_impact_cov: Optional[np.ndarray] = None
    if feasible_offsets:
        best_feasible_p = -1.0
        for i, entry in enumerate(envelope):
            if entry.smoothed_p_hit >= threshold and entry.optimal_p_hit > best_feasible_p:
                best_feasible_p = entry.optimal_p_hit
                wr = window_results[i]
                best_impact_mean = wr.optimal_impact_mean
                best_impact_cov = wr.optimal_impact_cov

    return ReleaseEnvelopeResult(
        envelope=envelope,
        feasible_offsets=feasible_offsets,
        corridor_ranges=corridor_ranges,
        heatmap=heatmap,
        heatmap_offsets=heatmap_offsets,
        heatmap_times=heatmap_times,
        impact_mean=best_impact_mean,
        impact_cov=best_impact_cov,
    )

