import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, RadioButtons
from configs import mission_configs as cfg
from src import decision_logic


def launch_ui(impact_points, P_hit, cep50):
    impact_points = np.asarray(impact_points, dtype=float)
    target_center = np.array(cfg.target_pos, dtype=float).flatten()[:2]
    target_radius = cfg.target_radius

    fig, ax = plt.subplots(figsize=(8, 8))
    plt.subplots_adjust(left=0.25, bottom=0.3)

    ax.scatter(impact_points[:, 0], impact_points[:, 1], alpha=0.4)
    ax.add_patch(
        plt.Circle(target_center, target_radius, color="r", fill=False)
    )
    ax.scatter(target_center[0], target_center[1], color="r", zorder=5)
    ax.set_xlabel("X Position (m)")
    ax.set_ylabel("Y Position (m)")
    ax.axis("equal")
    ax.grid(True)

    title = ax.set_title("")

    ax_slider = plt.axes([0.25, 0.15, 0.6, 0.03])
    threshold_slider = Slider(
        ax=ax_slider,
        label="Probability Threshold (%)",
        valmin=cfg.THRESHOLD_SLIDER_MIN,
        valmax=cfg.THRESHOLD_SLIDER_MAX,
        valinit=cfg.THRESHOLD_SLIDER_INIT,
        valstep=cfg.THRESHOLD_SLIDER_STEP,
    )

    ax_radio = plt.axes([0.025, 0.4, 0.18, 0.2])
    mode_radio = RadioButtons(
        ax_radio,
        tuple(cfg.MODE_THRESHOLDS.keys()),
        active=1,
    )

    def update_display(val=None):
        probability_threshold = threshold_slider.val / 100.0
        decision = decision_logic.evaluate_drop_decision(
            P_hit, probability_threshold
        )
        hit_pct = P_hit * 100.0
        threshold_pct = probability_threshold * 100.0
        mode_label = mode_radio.value_selected
        title.set_text(
            f"Mode: {mode_label} | Decision: {decision} | "
            f"Target Hit % = {hit_pct:.1f} | "
            f"Threshold = {threshold_pct:.1f}% | CEP50 = {cep50:.2f} m"
        )
        fig.canvas.draw_idle()

    def on_mode_clicked(label):
        threshold_slider.set_val(
            cfg.MODE_THRESHOLDS[label] * 100.0
        )
        update_display()

    threshold_slider.on_changed(update_display)
    mode_radio.on_clicked(on_mode_clicked)

    update_display()
    plt.show()
