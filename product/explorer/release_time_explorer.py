"""
Release-time explorer using Unscented Transform impact prediction.

Scans a time grid of candidate release moments along the current UAV
trajectory and evaluates each using the UT propagator.  Returns the
feasible drop window, the optimal release time, and a per-timestep
results table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from product.guidance.advisory_layer import _run_monte_carlo_adaptive
from product.uncertainty.unscented_propagation import propagate_unscented
from src import metrics


@dataclass
class ReleaseTimeResult:
    """Single candidate release-time evaluation."""

    time: float
    p_hit: float
    impact_mean: np.ndarray
    impact_cov: np.ndarray
    p_hit_ut: float = 0.0
    p_hit_mc: Optional[float] = None


@dataclass
class ReleaseWindowResult:
    """Aggregate output of the release-window search."""

    release_window: List[Tuple[float, float]]
    optimal_release_time: float
    optimal_p_hit: float
    results_table: List[ReleaseTimeResult]
    optimal_p_hit_ut: float = 0.0
    optimal_p_hit_mc: Optional[float] = None


def _group_into_intervals(
    times: List[float],
    tolerance: float,
) -> List[Tuple[float, float]]:
    """Group sorted feasible times into contiguous (start, end) intervals."""
    if not times:
        return []
    intervals: List[Tuple[float, float]] = []
    start = times[0]
    prev = times[0]
    for t in times[1:]:
        if t - prev > tolerance * 1.5:
            intervals.append((start, prev))
            start = t
        prev = t
    intervals.append((start, prev))
    return intervals


class _ContextWithAltitude:
    """Lightweight wrapper that adds a release_altitude attribute to an
    existing PropagationContext without mutating it."""

    def __init__(self, ctx, release_altitude: float):
        self._ctx = ctx
        self.release_altitude = float(release_altitude)

    def __getattr__(self, name):
        return getattr(self._ctx, name)


def find_release_window(
    context,
    config,
    pos0,
    vel0,
    target_pos,
    motion_predictor=None,
) -> ReleaseWindowResult:
    """
    Scan a time grid for feasible release moments and identify the optimal one.

    Parameters
    ----------
    context :
        PropagationContext (immutable).
    config :
        Configuration object with at least:
            max_release_time        – upper bound of search grid (s).
            release_time_step       – grid spacing (s).
            drop_probability_threshold – minimum P_hit for feasibility.
            enable_hybrid_estimation – if True, run adaptive MC for up to
                max_mc_verifications candidate cells (P_hit_ut > 0.25 * threshold
                or top 5 by P_hit_ut, ranked by P_hit_ut); store p_hit_ut and
                p_hit_mc; advisory uses MC when available.
        Also carries UT uncertainty parameters; for hybrid: wind_std, random_seed.
    pos0 : array_like, shape (3,)
        Current UAV position.
    vel0 : array_like, shape (3,)
        Current UAV velocity (assumed constant over the search horizon).
    target_pos : array_like, shape (2,) or (3,)
        Target position; only the first two components (x, y) are used.
    motion_predictor : optional
        If provided, uses MotionPredictor for kinematic extrapolation instead of
        constant velocity. Uses pos_release, vel_release from predict_state with
        actuator delay applied.

    Returns
    -------
    ReleaseWindowResult
        release_window         – list of (start, end) time intervals where
                                 P_hit >= threshold.
        optimal_release_time   – time with highest P_hit.
        optimal_p_hit          – P_hit at that time.
        results_table          – per-timestep ReleaseTimeResult entries.
    """
    pos0 = np.asarray(pos0, dtype=float).reshape(3)
    vel0 = np.asarray(vel0, dtype=float).reshape(3)
    target_2d = np.asarray(target_pos, dtype=float).flatten()[:2]

    t_min = 0.0
    t_max = float(getattr(config, "max_release_time", 5.0))
    dt_search = float(getattr(config, "release_time_step", 0.1))
    threshold = float(getattr(config, "drop_probability_threshold", 0.5))
    target_radius = float(getattr(config, "target_radius", 0.0))
    release_delay = float(getattr(config, "release_delay", 0.1))

    # ------------------------------------------------------------------
    # Stage 1: coarse scan over the full horizon.
    # ------------------------------------------------------------------
    coarse_times = np.arange(t_min, t_max + dt_search * 0.5, dt_search)

    results_table: List[ReleaseTimeResult] = []
    best_t = 0.0
    best_p = -1.0
    evaluated_times: dict[float, ReleaseTimeResult] = {}

    def _evaluate_time(t: float) -> ReleaseTimeResult:
        if motion_predictor is not None:
            t_now = motion_predictor.vehicle_state.timestamp
            pos_future, vel_future = motion_predictor.predict_state(t_now + float(t))
            pos_release, vel_release = motion_predictor.predict_state(
                t_now + float(t) + release_delay
            )
        else:
            pos_future = pos0 + vel0 * float(t)
            pos_release = pos_future + vel0 * release_delay
            vel_release = vel0

        # Wrap context so the UT uncertainty model sees the projected
        # release altitude for altitude-dependent wind uncertainty.
        ctx_t = _ContextWithAltitude(context, release_altitude=float(pos_release[2]))

        impact_mean, impact_cov = propagate_unscented(
            ctx_t, config, pos_release, vel_release,
        )

        # Stabilize covariance and compute Mahalanobis distance via linear solve.
        delta = impact_mean - target_2d
        cov_stable = impact_cov + 1e-12 * np.eye(2, dtype=float)
        x = np.linalg.solve(cov_stable, delta)
        d2 = float(delta @ x)

        # Directional variance along miss direction for finite target radius.
        norm_delta = float(np.linalg.norm(delta))
        if norm_delta < 1e-6:
            sigma2 = float(np.trace(impact_cov) / 2.0)
        else:
            u = delta / norm_delta
            sigma2 = float(u @ impact_cov @ u)
        sigma2 = max(sigma2, 1e-12)
        if target_radius > 0.0:
            radius_term = 1.0 - float(np.exp(-(target_radius ** 2) / (2.0 * sigma2)))
        else:
            radius_term = 1.0

        p_hit_ut = float(np.exp(-0.5 * d2) * radius_term)

        return ReleaseTimeResult(
            time=float(t),
            p_hit=p_hit_ut,
            impact_mean=impact_mean,
            impact_cov=impact_cov,
            p_hit_ut=p_hit_ut,
            p_hit_mc=None,
        )

    def _get_entry(t: float) -> ReleaseTimeResult:
        if t not in evaluated_times:
            evaluated_times[t] = _evaluate_time(t)
        return evaluated_times[t]

    for t in coarse_times:
        entry = _get_entry(float(t))
        results_table.append(entry)
        if entry.p_hit > best_p:
            best_p = entry.p_hit
            best_t = entry.time

    # ------------------------------------------------------------------
    # Stage 2: local refinement around the best coarse time.
    # ------------------------------------------------------------------
    dt_refine = dt_search / 5.0
    t_refine_min = max(t_min, best_t - dt_search)
    t_refine_max = min(t_max, best_t + dt_search)
    refine_times = np.arange(t_refine_min, t_refine_max + dt_refine * 0.5, dt_refine)

    for t in refine_times:
        entry = _get_entry(float(t))
        results_table.append(entry)
        if entry.p_hit > best_p:
            best_p = entry.p_hit
            best_t = entry.time

    # ------------------------------------------------------------------
    # Stage 3 (optional): hybrid UT–MC — run adaptive MC for candidate cells.
    # ------------------------------------------------------------------
    enable_hybrid = bool(getattr(config, "enable_hybrid_estimation", False))
    wind_std = float(getattr(config, "wind_std", None) or getattr(config, "wind_sigma0", 1.0))
    random_seed = int(getattr(config, "random_seed", 42))
    base_config = (
        config
        if isinstance(config, dict)
        else {"n_samples": int(getattr(config, "n_samples", 500))}
    )

    if enable_hybrid and target_radius > 0:
        candidate_threshold = 0.25 * threshold
        max_mc_verifications = int(getattr(config, "max_mc_verifications", 10))
        sorted_by_ut = sorted(
            enumerate(results_table),
            key=lambda x: x[1].p_hit_ut,
            reverse=True,
        )
        top_5_indices = {i for i, _ in sorted_by_ut[:5]}
        candidates = [
            i for i, e in enumerate(results_table)
            if e.p_hit_ut > candidate_threshold or i in top_5_indices
        ]
        candidates_sorted = sorted(
            candidates, key=lambda i: results_table[i].p_hit_ut, reverse=True
        )
        mc_indices = set(candidates_sorted[:max_mc_verifications])
        target_3d = np.array([target_2d[0], target_2d[1], 0.0], dtype=float)

        for i, entry in enumerate(results_table):
            if i not in mc_indices:
                continue

            pos_future = pos0 + vel0 * float(entry.time)
            pos_release = pos_future + vel0 * release_delay

            impact_points, _ = _run_monte_carlo_adaptive(
                context,
                pos_release,
                vel0,
                wind_std,
                base_config,
                random_seed,
                target_3d,
                target_radius,
                caller="BASE",
                mode="advanced",
            )
            p_hit_mc = metrics.compute_hit_probability(
                impact_points, target_3d, target_radius
            )
            entry.p_hit_mc = float(p_hit_mc)
            entry.p_hit = float(p_hit_mc)

        # Recompute best and feasible using effective p_hit (MC when available).
        best_p = -1.0
        best_t = 0.0
        best_entry = None
        for r in results_table:
            if r.p_hit > best_p:
                best_p = r.p_hit
                best_t = r.time
                best_entry = r
        feasible_times = sorted(r.time for r in results_table if r.p_hit >= threshold)
        release_window = _group_into_intervals(feasible_times, dt_search)
        optimal_p_hit_ut = best_entry.p_hit_ut if best_entry else 0.0
        optimal_p_hit_mc = best_entry.p_hit_mc if best_entry else None
    else:
        feasible_times = sorted(r.time for r in results_table if r.p_hit >= threshold)
        release_window = _group_into_intervals(feasible_times, dt_search)
        best_entry = next((r for r in results_table if r.time == best_t), None)
        optimal_p_hit_ut = best_entry.p_hit_ut if best_entry else best_p
        optimal_p_hit_mc = None

    return ReleaseWindowResult(
        release_window=release_window,
        optimal_release_time=best_t,
        optimal_p_hit=best_p,
        results_table=results_table,
        optimal_p_hit_ut=optimal_p_hit_ut,
        optimal_p_hit_mc=optimal_p_hit_mc,
    )
