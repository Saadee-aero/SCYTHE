"""
Monte Carlo uncertainty propagation — batch vectorized engine.
Same physics as propagate_payload; all samples computed in parallel.

Physics model (Level-3):
  - Exponential atmosphere (altitude-dependent density)
  - Linear wind shear (altitude-dependent wind)
  - RK2 (Heun) integrator
  - Fully batch-vectorized over N samples
"""
import time
import numpy as np
from src import physics
from product.physics.wind_model import (
    generate_correlated_wind_profiles_batch,
    generate_wind_drift_batch,
)


# Gravity vector (SI)
_GRAVITY = np.array([0.0, 0.0, -9.81], dtype=float)


def _draw_wind_sample(rng, wind_mean, wind_std):
    """Draw one wind vector (3,) from Gaussian. SI: m/s."""
    wind_mean = np.asarray(wind_mean, dtype=float).reshape(3)
    return wind_mean + rng.normal(0, wind_std, size=3)


def _draw_wind_batch(rng, wind_mean, wind_std, n_samples):
    """Draw N wind vectors (N, 3) from Gaussian. SI: m/s. Deterministic for same seed."""
    wind_mean = np.asarray(wind_mean, dtype=float).reshape(3)
    return wind_mean + rng.normal(0, wind_std, size=(n_samples, 3))


def _impact_xy(trajectory):
    """Extract final (x, y) from trajectory (N, 3). Returns (2,) or None if empty."""
    if trajectory.shape[0] == 0:
        return None
    return trajectory[-1, :2].copy()


def _impact_speed(trajectory, dt, pos0, vel0):
    """Compute impact velocity magnitude from trajectory (post-processing only)."""
    if trajectory.shape[0] == 0:
        return float(np.linalg.norm(vel0))
    if trajectory.shape[0] >= 2:
        disp = trajectory[-1] - trajectory[-2]
        return float(np.linalg.norm(disp / dt))
    return float(np.linalg.norm(vel0))


def _compute_acceleration(pos, vel, context, wind_a=None):
    """
    Compute acceleration from context (density, wind, mass, Cd, area).

    Args:
        pos: (M, 3) positions
        vel: (M, 3) velocities
        context: PropagationContext with density, wind, mass, Cd, area
        wind_a: optional (M, 3) pre-resolved wind; if provided, skips context.wind()

    Returns:
        acc: (M, 3) acceleration vectors
    """
    z = pos[:, 2]
    rho = context.density(z)
    # If wind_a is provided, do NOT call context.wind(z).
    # This prevents double wind computation inside masked RK2 path.
    if wind_a is None:
        wind = context.wind(z)
    else:
        wind = wind_a
        if __debug__:
            assert wind_a.shape == (len(z), 3)
    wind = np.asarray(wind, dtype=float)
    if __debug__:
        assert rho.ndim == 1
        assert rho.shape[0] == len(z)
    v_rel = vel - wind
    v_rel_mag = np.linalg.norm(v_rel, axis=1)
    drag_force = np.where(
        v_rel_mag[:, None] > 0,
        -0.5 * rho[:, None] * context.Cd * context.area * v_rel_mag[:, None] * v_rel,
        0.0,
    )
    return _GRAVITY + drag_force / context.mass


def _propagate_payload_batch(
    context,
    pos0,
    vel0,
    return_trajectories=False,
    return_impact_speeds=False,
    use_precise_impact=False,
    pos0_batch=None,
    vel0_batch=None,
    wind_drift_amp=None,
    wind_drift_omega=0.0,
    wind_drift_phase=None,
):
    """
    Batch vectorized propagation with RK2 (Heun) integration.

    Physics:
      - Altitude-dependent density (exponential atmosphere)
      - Wind: via context.wind(z) (correlated profiles or linear shear)
      - Optional sinusoidal temporal wind drift
      - RK2 (Heun) integrator

    context: PropagationContext with mass, Cd, area, wind_ref, shear, target_z, dt,
        and optional wind_profiles, z_levels.
    pos0, vel0: (3,). context.wind_ref: (N, 3) ground-level wind per sample.
    use_precise_impact: if True, linearly interpolate impact position.
    pos0_batch: (N, 3) per-sample initial positions, or None (broadcast pos0).
    vel0_batch: (N, 3) per-sample initial velocities, or None (broadcast vel0).
    wind_drift_amp: (N, 3) per-sample drift amplitude, or None (no drift).
    wind_drift_omega: scalar angular frequency for sinusoidal drift.
    wind_drift_phase: (N, 3) per-sample phase offset, or None.
    Returns impact_xy (N, 2), optionally trajectories list, impact_speeds (N,).
    """
    if __debug__:
        assert context.shear.shape == (3,)
        assert isinstance(context.dt, (int, float))
        assert context.mass > 0

    ground_z = float(context.target_z)
    dt = float(context.dt)

    use_profiles = context.wind_profiles is not None and context.z_levels is not None

    N = context.wind_ref.shape[0]
    if pos0_batch is not None:
        pos = np.asarray(pos0_batch, dtype=float).reshape(N, 3).copy()
    else:
        pos = np.broadcast_to(np.asarray(pos0, dtype=float).reshape(3), (N, 3)).copy()
    if vel0_batch is not None:
        vel = np.asarray(vel0_batch, dtype=float).reshape(N, 3).copy()
    else:
        vel = np.broadcast_to(np.asarray(vel0, dtype=float).reshape(3), (N, 3)).copy()

    impact_xy = np.full((N, 2), np.asarray(pos0)[:2], dtype=float)
    impact_stored = np.zeros(N, dtype=bool)

    impact_speeds_out = np.zeros(N, dtype=float) if return_impact_speeds else None

    max_steps = 25000
    traj = np.zeros((max_steps, N, 3), dtype=float) if return_trajectories else None
    step_count = np.zeros(N, dtype=int) if return_trajectories else None

    use_drift = wind_drift_amp is not None and wind_drift_phase is not None

    step = 0
    t_sim = 0.0
    active = pos[:, 2] > ground_z

    while np.any(active):
        p_a = pos[active]
        v_a = vel[active]

        wind_1 = context.wind_for_mask(p_a[:, 2], active)
        if use_drift:
            drift_1 = wind_drift_amp[active] * np.sin(
                wind_drift_omega * t_sim + wind_drift_phase[active]
            )
            wind_1 = wind_1 + drift_1

        # --- RK2 (Heun) ---
        a1 = _compute_acceleration(p_a, v_a, context, wind_a=wind_1)

        # Predictor: Euler step
        v_temp = v_a + a1 * dt
        p_temp = p_a + v_a * dt

        wind_2 = context.wind_for_mask(p_temp[:, 2], active)
        if use_drift:
            drift_2 = wind_drift_amp[active] * np.sin(
                wind_drift_omega * (t_sim + dt) + wind_drift_phase[active]
            )
            wind_2 = wind_2 + drift_2

        a2 = _compute_acceleration(p_temp, v_temp, context, wind_a=wind_2)

        # Corrector: average of both stages
        vel[active] = v_a + 0.5 * (a1 + a2) * dt
        pos[active] = p_a + 0.5 * (v_a + v_temp) * dt

        # --- Store trajectories if requested ---
        if return_trajectories and step < max_steps:
            traj[step, active, :] = pos[active]
            step_count[active] = step + 1

        # --- Detect ground impact ---
        active_new = pos[:, 2] > ground_z
        just_hit = active & ~active_new

        if use_precise_impact and np.any(just_hit):
            # Linear interpolation: alpha = (p_prev_z - ground_z) / (p_prev_z - p_curr_z)
            p_prev = p_a[just_hit[active]]
            p_curr = pos[just_hit]
            denom = p_prev[:, 2] - p_curr[:, 2]
            alpha = np.where(
                denom > 0,
                (p_prev[:, 2] - ground_z) / denom,
                1.0,
            )
            alpha = np.clip(alpha, 0.0, 1.0)
            p_impact = p_prev + alpha[:, None] * (p_curr - p_prev)
            impact_xy[just_hit, 0] = p_impact[:, 0]
            impact_xy[just_hit, 1] = p_impact[:, 1]
        else:
            impact_xy[just_hit, 0] = pos[just_hit, 0]
            impact_xy[just_hit, 1] = pos[just_hit, 1]
        impact_stored[just_hit] = True

        if return_impact_speeds:
            impact_speeds_out[just_hit] = np.linalg.norm(vel[just_hit], axis=1)

        active = active_new
        step += 1
        t_sim += dt

        if return_trajectories and step >= max_steps:
            remaining = active.copy()
            impact_xy[remaining] = pos[remaining, :2]
            impact_stored[remaining] = True
            if return_impact_speeds:
                impact_speeds_out[remaining] = np.linalg.norm(vel[remaining], axis=1)
            break

    # Fallback for samples that never hit (e.g. z0 <= ground_z)
    impact_xy[~impact_stored] = np.asarray(pos0)[:2]

    if return_trajectories:
        trajectories_out = [
            traj[: step_count[i], i, :].copy() for i in range(N)
        ]
        return (impact_xy, trajectories_out, impact_speeds_out)

    return (impact_xy, impact_speeds_out)


def run_monte_carlo(
    context,
    pos0,
    vel0,
    wind_std,
    config,
    random_seed,
    return_trajectories=False,
    return_impact_speeds=False,
    *,
    correlation_length=None,
    use_precise_impact: bool = False,
    caller: str = "BASE",
    mode: str = "advanced",
    sensor_model=None,
    release_sigma=None,
    time_varying_wind: bool = False,
    wind_drift_amplitude: float | None = None,
    wind_drift_period: float | None = None,
):
    """
    Monte Carlo uncertainty propagation. One wind sample per trajectory.
    Same seed gives same results. Returns impact points (N, 2);
    optionally full trajectories. When return_impact_speeds=True,
    also returns impact_speeds (N,) in m/s. All inputs explicit. SI units.

    Physics (Level-3):
      - Exponential atmosphere (altitude-dependent density)
      - Wind: correlated vertical profiles (if correlation_length set) or linear shear
      - RK2 (Heun) integrator
      - rho kept in signature for API compatibility; density from atmosphere model

    context: PropagationContext (mass, Cd, area, wind_ref, shear, target_z, dt).
        Built by advisory layer. wind_ref used as wind_mean for sampling.
    correlation_length: vertical correlation scale (meters) for AR(1) wind model.
        When set, generates altitude-dependent correlated wind profiles per sample.
        When None, falls back to linear shear model.
    target_z, dt, shear: from context.
    use_precise_impact: if True, linearly interpolate impact xy at ground crossing.
    sensor_model: optional SensorModel instance.  When provided, generates
        per-sample perturbed initial conditions (pos, vel) from the truth
        state.  When None, all samples share the same initial conditions
        (legacy behaviour).
    release_sigma: optional release-timing jitter 1-sigma (seconds).
        When > 0, each sample draws dt_jitter ~ N(0, release_sigma) and
        shifts the initial position: pos0_sample = pos0 + vel0 * dt_jitter.
        Fully vectorized (shape (N,)), no Python loop.
        Applied after sensor_model perturbation (if any).
        When None or 0, behaviour is unchanged.
    time_varying_wind: if True, add sinusoidal temporal wind drift during
        propagation.  Per-sample amplitude/phase sampled from RNG; period
        shared.  Default False (no drift, identical behaviour).
    wind_drift_amplitude: 1-sigma of drift amplitude Gaussian (m/s).
        Default 0.5 when time_varying_wind is True.
    wind_drift_period: period of sinusoidal drift (s).  Default 10.0.

    config: dict-like with at least "n_samples". Sample count determined internally.
        Canonical default = 1000. No UI influence; single source of truth.

    Implementation: batch vectorized. No Python loop over samples.
    Complexity O(S) with S = integration steps (vs O(N*S) previously).

    AX-MC-CALL-TRACE-25: caller and mode for diagnostic tracing.
    """
    assert isinstance(random_seed, int), "random_seed must be int, never None"
    print(f"[SEED] Using random_seed = {random_seed}")

    from src.decision_doctrine import MIN_VALID_N

    n_samples = int(config.get("n_samples", 1000))
    _cfg_n = config.get("n_samples")
    if _cfg_n is not None and int(_cfg_n) < MIN_VALID_N:
        raise ValueError(
            f"Configured n_samples ({_cfg_n}) below statistical minimum ({MIN_VALID_N}). "
            "Adaptive sampling must respect MIN_VALID_N."
        )
    if n_samples < MIN_VALID_N:
        print(f"[MC] Adjusted N to MIN_VALID_N = {MIN_VALID_N}")
        n_samples = MIN_VALID_N
    print(f"[MC TRACE LIVE] caller={caller}")
    if caller != "BASE":
        raise RuntimeError(
            f"Illegal Monte Carlo call from {caller}. Only BASE allowed in LIVE mode."
        )
    print(f"[MC TRACE] caller={caller} | mode={mode} | N={n_samples}")
    pos0 = np.asarray(pos0, dtype=float).reshape(3)
    vel0 = np.asarray(vel0, dtype=float).reshape(3)
    wind_mean = np.asarray(context.wind_ref, dtype=float).reshape(3)
    rng = np.random.default_rng(seed=random_seed)

    # --- Sensor noise: per-sample initial conditions ---
    pos0_batch = None
    vel0_batch = None
    if sensor_model is not None and not sensor_model.is_zero:
        pos0_batch, vel0_batch = sensor_model.perturb_batch(
            pos0, vel0, n_samples, rng,
        )

    # --- Release timing jitter (vectorized, no loop) ---
    if release_sigma is not None and float(release_sigma) > 0:
        dt_jitter = rng.normal(0.0, float(release_sigma), size=n_samples)
        if pos0_batch is not None:
            pos0_batch += vel0_batch * dt_jitter[:, None]
        else:
            vel0_arr = np.broadcast_to(vel0, (n_samples, 3))
            pos0_batch = (
                np.broadcast_to(pos0, (n_samples, 3)).copy()
                + vel0_arr * dt_jitter[:, None]
            )

    # --- Temporal wind drift parameters (if requested) ---
    wd_amp = None
    wd_omega = 0.0
    wd_phase = None
    if time_varying_wind:
        _drift_amp_std = float(wind_drift_amplitude if wind_drift_amplitude is not None else 0.5)
        _drift_period = float(wind_drift_period if wind_drift_period is not None else 10.0)
        wd_amp, wd_omega, wd_phase = generate_wind_drift_batch(
            rng, n_samples, drift_amplitude=_drift_amp_std, drift_period=_drift_period,
        )

    # --- Wind sampling (ground-level per-sample base) ---
    t0 = time.perf_counter()
    wind_samples = _draw_wind_batch(rng, wind_mean, wind_std, n_samples)
    wind_sampling_ms = (time.perf_counter() - t0) * 1000.0

    # --- Correlated wind profiles (if requested) ---
    t0 = time.perf_counter()
    wind_profiles = None
    z_levels = None
    if correlation_length is not None and correlation_length > 0:
        z_max = max(float(pos0[2]), 1.0)
        dz_res = 5.0
        z_levels = np.arange(0.0, z_max + dz_res, dz_res)
        wind_profiles = generate_correlated_wind_profiles_batch(
            z_levels, wind_samples, wind_std, correlation_length, rng,
        )
    uncertainty_sampling_ms = (time.perf_counter() - t0) * 1000.0

    mc_context = context.with_wind(wind_samples, wind_profiles, z_levels)

    # --- Propagation ---
    t0 = time.perf_counter()
    result = _propagate_payload_batch(
        mc_context,
        pos0, vel0,
        return_trajectories=return_trajectories,
        return_impact_speeds=return_impact_speeds,
        use_precise_impact=use_precise_impact,
        pos0_batch=pos0_batch,
        vel0_batch=vel0_batch,
        wind_drift_amp=wd_amp,
        wind_drift_omega=wd_omega,
        wind_drift_phase=wd_phase,
    )
    propagation_ms = (time.perf_counter() - t0) * 1000.0

    # --- Impact extraction ---
    t0 = time.perf_counter()
    impact_array = result[0].reshape(n_samples, 2)
    impact_processing_ms = (time.perf_counter() - t0) * 1000.0

    total_ms = wind_sampling_ms + uncertainty_sampling_ms + propagation_ms + impact_processing_ms
    print(f"[MC INTERNAL] wind_sampling={wind_sampling_ms:.3f} ms")
    print(f"[MC INTERNAL] uncertainty_sampling={uncertainty_sampling_ms:.3f} ms")
    print(f"[MC INTERNAL] propagation={propagation_ms:.3f} ms")
    print(f"[MC INTERNAL] impact_processing={impact_processing_ms:.3f} ms")
    print(f"[Monte Carlo] N={n_samples} elapsed={total_ms:.2f} ms")

    if return_trajectories:
        trajectories_out = result[1]
        impact_speeds_out = result[2]
        if return_impact_speeds:
            return impact_array, trajectories_out, impact_speeds_out
        return impact_array, trajectories_out

    if return_impact_speeds:
        return impact_array, result[1]

    return impact_array
