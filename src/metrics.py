import numpy as np


def _clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(value)))


def compute_hit_probability(impact_points, target_position, target_radius):
    """
    Target hit probability from impact points. Inputs must not be empty.
    Returns float in [0, 1].
    """
    impact_points = np.asarray(impact_points, dtype=float)
    target_position = np.asarray(target_position, dtype=float)
    if impact_points.shape[0] == 0:
        raise ValueError("impact_points must not be empty")
    if impact_points.shape[1] != 2:
        raise ValueError("impact_points must have shape (N, 2)")
    target_2d = np.asarray(target_position, dtype=float).flatten()[:2]
    radius = float(target_radius)
    radial_distances = np.linalg.norm(impact_points - target_2d, axis=1)
    hits = np.sum(radial_distances <= radius)
    return float(hits) / float(impact_points.shape[0])


def compute_cep50(impact_points, target_position):
    """
    CEP50: 50th percentile of radial miss distance from target.
    Inputs must not be empty. Returns CEP50 radius in same units as inputs.
    """
    impact_points = np.asarray(impact_points, dtype=float)
    target_position = np.asarray(target_position, dtype=float)
    if impact_points.shape[0] == 0:
        raise ValueError("impact_points must not be empty")
    if impact_points.shape[1] != 2:
        raise ValueError("impact_points must have shape (N, 2)")
    target_2d = np.asarray(target_position, dtype=float).flatten()[:2]
    radial_distances = np.linalg.norm(impact_points - target_2d, axis=1)
    return float(np.percentile(radial_distances, 50))


def compute_impact_velocity_stats(impact_speeds):
    """
    Aggregate impact velocity statistics from Monte Carlo samples.
    Returns dict with mean_impact_speed, std_impact_speed,
    p95_impact_speed (m/s).
    """
    impact_speeds = np.asarray(impact_speeds, dtype=float)
    if impact_speeds.size == 0:
        return {
            "mean_impact_speed": 0.0,
            "std_impact_speed": 0.0,
            "p95_impact_speed": 0.0,
        }
    return {
        "mean_impact_speed": float(np.mean(impact_speeds)),
        "std_impact_speed": float(np.std(impact_speeds)),
        "p95_impact_speed": float(np.percentile(impact_speeds, 95)),
    }


def compute_confidence_index(
    wind_std,
    ballistic_coefficient,
    altitude,
    telemetry_freshness,
):
    """
    Numerical confidence index in [0, 1] for decision transparency.

    Factors (equal weights):
    - wind_factor: exp(-k1 * wind_std)
    - bc_factor: clamp(ballistic_coefficient / BC_ref, 0, 1)
    - altitude_factor: clamp(1 - altitude / altitude_limit, 0, 1)
    - telemetry_factor: 1 if fresh else 0.5
    """
    k1 = 0.35
    bc_ref = 120.0
    altitude_limit = 3000.0
    freshness_limit_s = 5.0

    wind_factor = float(np.exp(-k1 * float(wind_std)))

    if ballistic_coefficient is None:
        bc_factor = 0.5
    else:
        bc_factor = _clamp(float(ballistic_coefficient) / bc_ref)

    altitude_factor = _clamp(1.0 - (float(altitude) / altitude_limit))

    is_fresh = False
    if telemetry_freshness is None:
        is_fresh = False
    elif isinstance(telemetry_freshness, bool):
        is_fresh = telemetry_freshness
    else:
        try:
            is_fresh = float(telemetry_freshness) <= freshness_limit_s
        except (TypeError, ValueError):
            is_fresh = False
    telemetry_factor = 1.0 if is_fresh else 0.5

    confidence = (
        wind_factor + bc_factor + altitude_factor + telemetry_factor
    ) / 4.0
    return _clamp(confidence)
