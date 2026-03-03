"""CLI and compatibility entrypoint for AIRDROP-X."""

from __future__ import annotations

import sys

from configs import mission_configs as cfg
from src import metrics
from product.payloads.payload_base import Payload
from product.missions.target_manager import Target
from product.missions.environment import Environment
from product.missions.mission_state import MissionState
from product.guidance.advisory_layer import (
    evaluate_advisory,
    get_impact_points_and_metrics,
)
from product.ui.ui_layout import launch_unified_ui


def _build_mission_state(payload_config=None):
    """Build a MissionState using config defaults or payload override."""
    if payload_config:
        payload = Payload(
            mass=payload_config.get("mass", cfg.mass),
            drag_coefficient=payload_config.get("drag_coefficient", cfg.Cd),
            reference_area=payload_config.get("reference_area", cfg.A),
        )
    else:
        payload = Payload(
            mass=cfg.mass,
            drag_coefficient=cfg.Cd,
            reference_area=cfg.A,
        )

    target = Target(position=cfg.target_pos, radius=cfg.target_radius)
    environment = Environment(wind_mean=cfg.wind_mean, wind_std=cfg.wind_std)

    return MissionState(
        payload=payload,
        target=target,
        environment=environment,
        uav_position=cfg.uav_pos,
        uav_velocity=cfg.uav_vel,
    )


def run_simulation(payload_config=None):
    """Run one full simulation cycle and return result tuple."""
    print("\n--- Starting Simulation ---")
    if payload_config:
        print(f"Using Custom Payload: {payload_config.get('name', 'Unknown')}")

    mission_state = _build_mission_state(payload_config)
    impact_points, P_hit, cep50, impact_velocity_stats = get_impact_points_and_metrics(
        mission_state, cfg.RANDOM_SEED, {"n_samples": cfg.n_samples}
    )
    base_snapshot = {
        "impact_points": impact_points,
        "P_hit": P_hit,
        "cep50": cep50,
        "target_position": mission_state.target.position,
        "target_radius": mission_state.target.radius,
    }
    advisory_result = evaluate_advisory(
        base_snapshot,
        "Balanced",
    )

    m = mission_state.payload.mass
    cd = mission_state.payload.drag_coefficient
    area = mission_state.payload.reference_area
    bc = (m / (cd * area)) if (cd and area) else None
    altitude = mission_state.uav_position[2]
    confidence_index = metrics.compute_confidence_index(
        wind_std=cfg.wind_std,
        ballistic_coefficient=bc,
        altitude=altitude,
        telemetry_freshness=None,
    )

    print(f"  -> CEP50: {cep50:.2f} m")
    print(f"  -> P(Hit): {P_hit * 100:.1f} %")
    print(f"  -> Confidence Index: {confidence_index:.2f}")
    print(f"  -> Advisory: {advisory_result.current_feasibility}")

    return (
        impact_points,
        advisory_result,
        P_hit,
        cep50,
        impact_velocity_stats,
        confidence_index,
    )


def _launch_qt_app() -> int:
    from qt_app.main import main as qt_main

    return int(qt_main())


def main() -> int:
    """
    Entry point.

    - `python main.py --qt` launches the PySide6 desktop app.
    - `python main.py --matplotlib` launches legacy Matplotlib UI.
    - `python main.py` runs one CLI simulation snapshot.
    """
    use_qt = "--qt" in sys.argv
    use_matplotlib = "--matplotlib" in sys.argv

    if use_qt:
        return _launch_qt_app()

    if use_matplotlib:
        impacts, adv, p_hit, cep50, impact_velocity_stats, confidence_index = run_simulation()
        launch_unified_ui(
            impact_points=impacts,
            P_hit=p_hit,
            cep50=cep50,
            impact_velocity_stats=impact_velocity_stats,
            confidence_index=confidence_index,
            release_point=cfg.uav_pos[:2],
            wind_vector=cfg.wind_mean[:2],
            wind_mean=cfg.wind_mean,
            wind_std=cfg.wind_std,
            target_position=cfg.target_pos,
            target_radius=cfg.target_radius,
            mission_state=None,
            advisory_result=adv,
            initial_threshold_percent=cfg.THRESHOLD_SLIDER_INIT,
            initial_mode="standard",
            slider_min=cfg.THRESHOLD_SLIDER_MIN,
            slider_max=cfg.THRESHOLD_SLIDER_MAX,
            slider_step=cfg.THRESHOLD_SLIDER_STEP,
            mode_thresholds=cfg.MODE_THRESHOLDS,
            on_threshold_change=lambda x: None,
            random_seed=cfg.RANDOM_SEED,
            n_samples=cfg.n_samples,
            dt=cfg.dt,
            run_simulation_callback=run_simulation,
        )
        return 0

    run_simulation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
