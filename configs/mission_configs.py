# Offline operation enforcement
OFFLINE_SAFE = True  # Enforce offline operation - no internet dependencies allowed

# Physical constants (SI)
g = 9.81
rho = 1.225

# Simulation
dt = 0.01

# Payload
mass = 1.0
A = 0.01
Cd = 1.0

# UAV initial state (m), (m/s)
uav_pos = (0.0, 0.0, 100.0)
uav_vel = (20.0, 0.0, 0.0)

# Target (m). target_pos = (x, y, z); z = target elevation (ground level for impact).
target_pos = (72.0, 0.0, 0.0)
target_radius = 5.0

# Wind model: mean (m/s), std (m/s)
wind_mean = (2.0, 0.0, 0.0)
wind_std = 0.8

# Monte Carlo. Canonical default sample count = 1000.
n_samples = 1000

# Reproducibility
RANDOM_SEED = 42

# Decision UI: threshold slider (%)
THRESHOLD_SLIDER_MIN = 50
THRESHOLD_SLIDER_MAX = 100
THRESHOLD_SLIDER_STEP = 0.5
THRESHOLD_SLIDER_INIT = 75

# Decision policy: hit probability threshold by mode
MODE_THRESHOLDS = {
    "Conservative": 0.90,
    "Balanced": 0.75,
    "Aggressive": 0.60,
}
