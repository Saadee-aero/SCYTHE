import numpy as np

GRAVITY_MAGNITUDE = 9.81


def propagate_payload(pos0, vel0, mass, Cd, A, rho, wind, dt, target_z=0.0):
    """
    3-DOF point-mass in inertial frame. X forward, Y lateral, Z up.
    Gravity constant in -Z. Drag = -0.5*rho*Cd*A*|v_rel|*v_rel,
    v_rel = vel - wind. Semi-Implicit (Symplectic) Euler. Stops when z <= target_z.
    target_z: ground level (m). Default 0.0.
    Returns trajectory (N, 3).
    """
    pos = np.array(pos0, dtype=float).copy()
    vel = np.array(vel0, dtype=float).copy()
    wind = np.array(wind, dtype=float)
    trajectory = []
    gravity = np.array([0.0, 0.0, -GRAVITY_MAGNITUDE])
    ground_z = float(target_z)
    while pos[2] > ground_z:
        v_rel = vel - wind
        v_rel_mag = np.linalg.norm(v_rel)
        if v_rel_mag > 0:
            drag_force = -0.5 * rho * Cd * A * v_rel_mag * v_rel
        else:
            drag_force = np.zeros(3)
        acc = gravity + drag_force / mass
        vel = vel + acc * dt
        pos = pos + vel * dt
        trajectory.append(pos.copy())
    if len(trajectory) == 0:
        return np.empty((0, 3))
    return np.array(trajectory, dtype=float).reshape(-1, 3)
