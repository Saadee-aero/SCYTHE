# SCYTHE — Claude Code Master Context File
# Stochastic Computed Yield and Terminal Hit Estimator
#
# READ THIS ENTIRE FILE BEFORE TOUCHING ANY CODE.
# Every decision here was made deliberately and is locked.
# Do not refactor, rename, or restructure anything not
# explicitly in your current task.
#
# AT THE END OF EVERY COMPLETED TASK: Update the Phase
# Completion Status section to reflect what was built.
# Mark completed items with CHECK. Add new decisions to
# the relevant section. Do not rewrite sections you did
# not touch.

---

## 1. Project Identity

Full Name: SCYTHE — Stochastic Computed Yield and Terminal Hit Estimator
Type: Semi-autonomous probabilistic UAV payload drop guidance system
Purpose: Precision unpowered payload delivery under real-world
atmospheric uncertainty. Operator-aided. NOT fully autonomous.
Hard Real-Time Target: 50ms end-to-end (target lock to advisory output)
Qt Binding: PySide6 ONLY. Never PyQt5. Never PyQt6.
Language: Python 3.x
UI Framework: PySide6 — QGraphicsView-based tactical map
Window Title: Must read "SCYTHE" not "SCYTHE"

---

## 2. Repository Structure

```
E:/SCYTHE/
|-- CLAUDE.md                          <- this file
|-- product/
|   |-- aircraft/
|   |   `-- motion_predictor.py        <- physics + uncertainty engine
|   |-- ui/
|   |   |-- widgets/
|   |   |   |-- tactical_map_widget.py <- QGraphicsView subclass (TacticalMapWidget)
|   |   |   |-- status_banner.py       <- StatusBannerWidget + DropStatus + DropReason
|   |   |   `-- hud_overlay.py         <- HUD QWidget overlay
|   |   |-- tabs/
|   |   |   `-- tactical_map_tab.py
|   |   |-- map_transform.py           <- MapTransform — DO NOT TOUCH
|   |   `-- tactical_map_controller.py
|   |-- runtime/
|   |   `-- runtime_loops.py           <- 4 runtime loops
|   `-- uncertainty/
|       `-- unscented_propagation.py   <- UT sigma point engine
|-- src/
|   `-- monte_carlo.py                 <- MC sampling engine
|-- qt_app/
|   |-- main_window.py                 <- main window, tab switching
|   `-- evaluation_worker.py           <- EvaluationWorker 6.6Hz
`-- configs/
    `-- mission_configs.py
```

---

## 3. Coordinate System — LOCKED. DO NOT TOUCH.

- ENU (East-North-Up) throughout. No internal conversions.
- MapTransform.pixels_per_meter is the ONLY zoom variable in the system.
  Do not introduce any other. Do not alias it. Do not copy it.
- MapTransform.apply_to_view handles Y-flip (Qt Y-axis inverted vs ENU).
- Do not modify MapTransform for any reason unless explicitly tasked.

---

## 4. Map Interaction — LOCKED. DO NOT TOUCH.

Input               | Action
--------------------|-------------------------
Ctrl + Scroll       | Zoom (cursor-anchored)
Scroll              | Pan
follow_uav          | True by default

- UAV marker: ItemIgnoresTransformations, fixed 15x14px
- Do not modify zoom or pan logic under any circumstances.

---

## 5. Runtime Architecture

Four runtime loops. Do not add loops. Do not change frequencies.

Loop                  | Frequency | Responsibility
----------------------|-----------|----------------------------------------
TelemetryLoop         | 50 Hz     | Sensor ingestion
GuidanceLoop          | 12 Hz     | Drop advisory, 50ms constraint
BackgroundPlannerLoop | 1 Hz      | Async MC planning (15-20s per cycle)
UIRenderLoop          | 30 Hz     | Display update

Workers:
- SimulationWorker — trajectory simulation
- EvaluationWorker — runs at 6.6Hz

State: Snapshot-driven immutable state.
SystemState uses threading.Lock. Never bypass this.

BackgroundPlannerLoop 15-20s per cycle is a known limitation.
Accepted as async advisory. Do not try to fix this unless tasked.

---

## 6. HUD Overlay — LOCKED

- HUD is a QWidget child of QGraphicsView.viewport() — NOT a scene item.
- Displays: Wind, Drag, Release, Vehicle bars + N compass
- Do not move HUD into the scene under any circumstances.
- Do not modify HUD in tasks that do not explicitly target it.

---

## 7. Complete Mathematics — SCYTHE Physics Engine

These are the implemented and validated math models.
Do not change physics logic without explicit instruction.

### 7.1 Vehicle Motion Model
Constant acceleration model used by MotionPredictor:
  x(t) = x0 + v0*t + 0.5*a*t^2
  v(t) = v0 + a*t
State vector: s = [x, y, z, vx, vy, vz]
Acceleration clamped: ||a|| <= 15 m/s^2

### 7.2 Relative Wind Velocity
  v_rel = v - v_wind
  v = ||v_rel||

### 7.3 Aerodynamic Drag
  F_d = -0.5 * rho * Cd * A * v * v_rel
  a_d = F_d / m
  rho = air density, Cd = drag coeff, A = reference area, v = speed

### 7.4 Gravity
  g = [0, 0, -9.81]
  a_total = g + a_d

### 7.5 Atmospheric Density — Exponential Model
  rho(z) = rho0 * exp(-z / H)
  rho0 = 1.225 kg/m^3
  H = 8500 m
  z_agl = z - z_ground

### 7.6 Rotational Payload Dynamics
  theta(t+1) = theta(t) + omega(t)*dt
  omega(t+1) = omega(t) - lambda*omega(t)*dt + sigma_w*sqrt(dt)*N(0,1)
  Cd_eff = Cd * (1 + k * |sin(theta)|)
  Cd_eff clamped: 0.5*Cd <= Cd_eff <= 2*Cd

### 7.7 Trajectory Integration — RK2
  k1 = f(x_n)
  k2 = f(x_n + k1*dt)
  x_{n+1} = x_n + (dt/2)*(k1 + k2)

### 7.8 Impact Detection — Ground Intersection
  alpha = (z_prev - z_ground) / (z_prev - z_curr)
  p_impact = p_prev + alpha*(p_curr - p_prev)

### 7.9 Unscented Transform — Sigma Point Propagation
  Sigma points: 2n+1
  x0 = mu
  x_i = mu + sqrt((n+lambda)*Sigma)_i
  x_{i+n} = mu - sqrt((n+lambda)*Sigma)_i
  Mean: mu = sum(W_i * x_i)
  Covariance: Sigma = sum(W_i * (x_i - mu)(x_i - mu)^T)

  Uncertainty vector (n=5, sigma points=11):
  u = [wind_x_bias, wind_y_bias, release_x, release_y, velocity_bias]

  Wind x/y off-diagonal correlation: Sigma[0,1]=Sigma[1,0]=0.3*var_wind
  Physical basis: heading estimation uncertainty ~17 deg creates correlated
  wx/wy bias errors. rho=0.3 is conservative fixed coefficient.
  Positive definiteness: det(2x2 wind block) = var_wind^2*(1-rho^2) > 0

  Sigma point propagations currently sequential.
  Optimization (batch all 11): PENDING — do not implement until profiled.

### 7.10 Impact Distribution
  Modeled as Gaussian: mean=mu_impact, covariance=Sigma_impact

### 7.11 Hit Probability — Mahalanobis Distance
  d^2 = (mu - t)^T * Sigma^{-1} * (mu - t)
  P_hit = exp(-0.5 * d^2)

### 7.12 Hit Probability — 2D MC Integration over UT Covariance
  Sample z ~ N(0, I), shape (N, 2)
  samples = mu_impact + L @ z.T  where L = cholesky(Sigma_impact + eps*I)
  P_hit = (1/N) * sum(||samples_i - target|| <= r)
  N = 2000, rng seeded once per find_release_window() call
  Fallback (Cholesky fail): point estimate — 1.0 if ||mu - target|| <= r else 0.0
  (Replaces heuristic exp(-0.5*d²)*(1-exp(-r²/2σ²)) — preserved as
  _compute_p_hit_heuristic_DEPRECATED in release_time_explorer.py)

### 7.13 CEP50 — Circular Error Probable
  CEP50 = 0.8326 * sqrt(lambda1 + lambda2)
  lambda1, lambda2 = eigenvalues of impact covariance matrix

### 7.14 Monte Carlo Hit Probability
  P_hit = (1/N) * sum(I(||x_i - t|| < r))
  Wind: AR(1) correlated, Gaussian uncertainty
  CI: Wilson Confidence Interval
  Current N=1000. Adaptive MC upgrade pending (see Section 10).

### 7.15 Actuator Delay — Release Position Shift
  p_release_actual = p_release + v_release * t_delay
  t_delay = 0.3s default (runtime_loops.py release_delay)
  Accounts for release mechanism servo + trigger latency.
  Implemented via existing `release_delay` config param in
  release_time_explorer.py (pos_release = pos_future + vel0 * release_delay).

---

## 8. Tab Navigation Reference

Index | Tab Name
------|--------------------
0     | Tactical Map
1     | Telemetry
2     | Mission Configuration
3     | Analysis
4     | System Status

Mission Configuration = index 2
Tactical Map = index 0

---

## 9. Phase Completion Status

### Phase 0 — COMPLETE
- [x] ENU coordinate system locked
- [x] MapTransform.pixels_per_meter as single zoom source of truth
- [x] Ctrl+scroll zoom cursor-anchored, scroll=pan
- [x] Dynamic grid spacing based on zoom level
- [x] HUD overlay as QWidget on viewport (not scene)
- [x] UAV marker ItemIgnoresTransformations, fixed 15x14px
- [x] follow_uav = True default
- [x] 5 critical bugs fixed: print statement, hard-coded physics,
      MotionPredictor wired, envelope_dirty flag, _busy try/finally
- [x] FPS counter removed
- [x] Camera feed reverted — not yet implemented

### Phase 1 — Operator Workflow — ACTIVE

#### Phase 1.1 — COMPLETE
- [x] StatusBannerWidget in product/ui/widgets/status_banner.py
- [x] DropStatus IntEnum: NO_DROP=0, APPROACH_CORRIDOR=1,
      IN_DROP_ZONE=2, DROP_NOW=3
- [x] Banner integrated into TacticalMapWidget as viewport child
- [x] _reposition_status_banner() for dynamic centering on resize
- [x] set_status() calls update() not repaint()
- [x] WA_TransparentForMouseEvents set (removed in 1.1b)
- [x] Label: background-color transparent, no text-transform
- [x] Compile-verified. Runtime-verified.

#### Phase 1.1b — COMPLETE
Context-aware and interactive status banner.

Requirements:
- Add DropReason IntEnum: NONE=0, MISSION_PARAMS_NOT_SET=1,
  UAV_TOO_FAR=2, WIND_EXCEEDED=3
- Advisory text line below main banner text (9pt, state-dependent)
- Banner height: 64px (was 44px)
- Remove WA_TransparentForMouseEvents
- mousePressEvent: emit navigate_to_tab(2) ONLY on
  MISSION_PARAMS_NOT_SET, do nothing on all other states
- wheelEvent: always call super() to pass scroll through
- Advisory blink on MISSION_PARAMS_NOT_SET: QTimer 1s toggle setVisible()
- Wire navigate_to_tab Signal to main window tab switcher
- Auto-redirect to Tactical Map after Commit Configuration button

Advisory text:
State + Reason               | Advisory Text                                   | Color
-----------------------------|------------------------------------------------|--------
NO_DROP+MISSION_PARAMS       | "Mission parameters not set — click to configure"| #FFDD00
NO_DROP+UAV_TOO_FAR          | "Outside drop corridor — adjust heading"         | white
NO_DROP+WIND_EXCEEDED        | "Wind envelope exceeded — hold position"         | white
NO_DROP+NONE                 | ""                                               | white
APPROACH_CORRIDOR            | "Intercept heading — maintain altitude"          | white
IN_DROP_ZONE                 | "Confirm release conditions"                     | white
DROP_NOW                     | "Release payload immediately"                    | black

#### Phase 1.2 — COMPLETE
- [x] Guidance arrow — update_guidance_arrow() called every frame from controller
      Projects UAV onto corridor centerline; arrow visible when outside corridor

#### Phase 1.3 — COMPLETE (colors and SIGMA_95 corrected)
- [x] Impact ellipses live update — ImpactEllipseLayer.update() bug fixed
- [x] UT covariance (2x2) → np.linalg.eigh() → semi-axes a, b in controller
- [x] Ellipse angle from dominant eigenvector
- [x] SIGMA_68=1.0 and SIGMA_95=2.448 named constants in tactical_map_widget.py
- [x] 3-sigma outer ellipse removed — SCYTHE uses 68% and 95% only
- [x] 68% color #00AA44 alpha 120; 95% color #FF8C00 alpha 80

#### Phase 1.4 — COMPLETE (colors corrected)
- [x] Corridor polygon from feasible_offsets + vehicle heading built in controller
- [x] ReleaseEnvelopeResult.impact_mean/impact_cov propagated from best entry UT
- [x] Corridor fill #00AA44 alpha 40; border #00AA44 alpha 180 width 1

#### Phase C — Math Fixes — COMPLETE
- [x] CEP50 constant fixed: 0.5*(a+b) → 0.8326*sqrt(a²+b²) in _update_cep_label
      (tactical_map_widget.py line 1066)
- [x] CLAUDE.md §7.13 constant updated from 0.5887 to 0.8326
- [x] P_hit heuristic replaced with _compute_p_hit_mc() in release_time_explorer.py
      _evaluate_time() now calls _compute_p_hit_mc with UT covariance and shared _rng
- [x] _compute_p_hit_heuristic_DEPRECATED preserved — do not delete until runtime validated
- [x] _rng created once per find_release_window() call, captured by _evaluate_time closure

#### Phase D — Adaptive Monte Carlo — COMPLETE
- [x] Adaptive MC already deployed end-to-end via _run_monte_carlo_adaptive
      through explorer path. No porting needed.
      Call chain: BackgroundPlannerLoop → compute_release_envelope →
      find_release_window → _run_monte_carlo_adaptive.
- [x] Added structured logger.info line in advisory_layer._run_monte_carlo_adaptive
      reporting final N, P_hit, CI_width for Section 11 validation.

### Phase 2 — Terrain — ACTIVE

#### Phase 2.1 — COMPLETE
- [x] TerrainModel class in product/terrain/terrain_model.py
      (get_elevation returns 0.0 flat; is_loaded returns False)
- [x] Terrain wired via PropagationContext.target_z, not MotionPredictor — spec corrected
- [x] BackgroundPlannerLoop accepts optional terrain param; defaults to TerrainModel()
- [x] Single TerrainModel instance created in run_scythe.py and passed into planner
- [x] Hardcoded target_z=0.0 in runtime_loops.py replaced with
      terrain.get_elevation(target_pos[0], target_pos[1])

- 2.2 SRTM tile loader
- 2.3 Physics engine ground_z from DEM

### Phase 3 — Sensor Layer — ACTIVE

#### Phase 3.1 — COMPLETE
- [x] product/sensors/ package created (CameraFeed re-exported from __init__.py)
- [x] CameraFeed in product/sensors/camera_feed.py — get_frame(width, height)
      returns solid dark-gray QImage (RGB32, QColor(40,40,40)) sized to caller;
      silent fail returns None. No-arg default 800x600 preserved as fallback.
- [x] CameraFeedLayer in tactical_map_widget.py — QWidget viewport child
      (same pattern as StatusBannerWidget / WindIndicatorLayer); setGeometry
      fills entire viewport via _reposition_camera_feed(). Draws QPixmap in
      paintEvent. Lowered via .lower() so status banner + wind indicator stay on top.
- [x] TacticalMapWidget.update_camera_feed(image) scales incoming QImage to
      viewport size with Qt.IgnoreAspectRatio + SmoothTransformation, then
      routes to layer.update_frame
- [x] TacticalMapController instantiates CameraFeed once; calls
      widget.update_camera_feed(feed.get_frame(vp.w, vp.h)) every _on_tick (30 Hz)
- [x] No georeferencing, no coordinate mapping; silent fail on None

- 3.2 Opportunity Explorer visualization
- 3.3 Camera-to-world coordinate bridge

### Phase 4 — Polish — ACTIVE

#### Phase 4.1 — COMPLETE
- [x] WindIndicatorLayer QWidget viewport child in tactical_map_widget.py
      (bottom-left, 120x100, white arrow + "{speed:.1f} m/s" 9pt label)
- [x] ENU -> Qt screen Y-flip: angle = atan2(-wind_y, wind_x)
- [x] Arrow length clamped 20-80 px, proportional to speed (*10)
- [x] _reposition_wind_indicator() called in resizeEvent
- [x] update_wind_indicator(wx, wy) wired from TacticalMapController on
      SystemState.wind_vector at 30 Hz tick
- Legacy WindHUDItem retired; WindIndicatorLayer is sole wind indicator.

#### Phase 4.2 — COMPLETE
- [x] TacticalMapController computes cep50_m = 0.8326 * sqrt(eigvals[0]+eigvals[1])
      using impact covariance eigenvalues directly (per §7.13)
- [x] TacticalMapWidget.update_cep_label(cep_m) public method
- [x] _cep_label recolored #00AA44, zValue 50 (was #00ff41 / 100)
- [x] Text: "CEP₅₀: {cep:.1f} m"; displays "CEP₅₀: ---" when cep_m > 999.9
- [x] Positioned 10 px below 68% ellipse center in scene coords
      (view Y-flip handled: scene -10 -> on-screen +10)
- [x] update_impact_ellipse now stores _last_ellipse_center_scene for CEP anchor

#### Phase 4.3 — COMPLETE
- [x] _compute_p_hit_heuristic_DEPRECATED deleted from
      product/explorer/release_time_explorer.py
- [x] Verified no call sites prior to deletion; _compute_p_hit_mc is the sole
      P_hit code path (release_time_explorer.py:227)
- [x] Codebase-wide DEPRECATED keyword sweep: no other deprecated functions in code
      (only CLAUDE.md history and .agent metadata reference the removed symbol)

#### Phase 4.4 — COMPLETE
- [x] AirdropMainWindow → ScytheMainWindow (qt_app.py + scythe_qt.spec.txt)
- [x] Window title already "SCYTHE" (qt_app.py:176, qt_app/main_window.py:160)
- [x] system_status tab already shows "SCYTHE v1.1" (system_status.py:23-24)
- Left unchanged (non-branding / intentional):
  - "airdrop" as generic domain term in propagation_context.py / atmosphere.py docstrings
  - spec/streamlit_baseline/*.reference (frozen historical reference files)
  - Filesystem paths E:\AIRDROP-X in .claude/settings.json and pyrightconfig.json

#### Phase 4.5 — COMPLETE
- [x] WIND_CORRELATION_RHO = 0.3 constant added at module level in
      product/uncertainty/unscented_state_model.py
- [x] Sigma[0,1] = Sigma[1,0] = rho * var_wind inserted after Sigma[1,1]
- [x] Symmetrize line left unchanged; assertion guard added after
      (WIND_CORRELATION_RHO < 1.0) for PD safety
- [x] §7.9 updated with wind correlation note and PD justification
- [x] Physical basis: heading uncertainty ~17 deg -> ρ=0.3 conservative
      det(2x2 wind block) = var_wind^2 * 0.91 > 0 confirmed PD

---

## 10. Planned Physics Upgrades — Do Not Implement Until Explicitly Tasked

### Plan A — Priority Upgrades
1. Adaptive Monte Carlo: DONE — deployed via _run_monte_carlo_adaptive
   (advisory_layer.py). Wilson CI early stopping, initial_batch=200,
   batch_size=100, min_samples=300, ci_width_target=0.05, max_samples≤1000.

2. Gust Model: Dryden or Von Karman turbulence.
   Adds gust_x(t), gust_y(t), gust_z(t) to wind.

3. Rotational Coupling: full omega_x, omega_y, omega_z state,
   drag torque, orientation-dependent Cd.

4. Actuator Delay: DONE — implemented via existing `release_delay` config
   parameter (runtime_loops.py:_Cfg.release_delay). Default raised 0.1→0.3s
   per §7.15. Applied at release_time_explorer.py lines 188/278 as
   pos_release = pos_future + vel0 * release_delay.

5. Release Corridor Solver: min/max range + optimal release point.

### Plan B — Advanced / Research Grade
6.  State-Dependent Uncertainty: sigma_wind(z) = a + b*z
7.  Gaussian Mixture Models: multi-modal wind distributions
8.  Particle Filter: sequential MC for live wind estimation
9.  Bayesian Hit Probability: Bayesian posterior replaces hits/N
10. Risk-Aware Advisory: expected loss over P_hit threshold
11. Moving Target Support: target velocity + future intercept distribution
12. Real-Time UAV Integration: PX4/ArduPilot MAVLink, GPS, IMU
13. Optimal Release Solver: argmax P_hit via gradient/CMA-ES
14. Multi-Payload Planning: coverage probability, multiple drops
15. DEM Terrain Interaction: SRTM elevation for impact prediction
16. Swarm Drop Planning: multi-UAV coordinated drop computation

---

## 11. Known Issues and Pending Fixes

- Sigma point propagations sequential — batch optimization pending profiling
- ImpactEllipseLayer.update() was misplaced in TargetMarker — FIXED Phase A1
- get_drop_status() in motion_predictor.py now maps guidance status string to DropStatus
  Real corridor/zone/timing logic requires GuidanceResult — not yet integrated as caller
- Validate adaptive MC N selection in live run — check logs confirm early stopping is
  triggering ("[MC ADAPTIVE] final N=... P_hit=... CI_width=..." in advisory_layer logger)

- src/physics.py and product/uncertainty/unscented_state_model.py also read target_z
  from context — they now inherit the TerrainModel-resolved value transitively; no
  additional wiring required. Tools/tests still hardcode target_z=0.0 (acceptable).

- CameraFeedLayer is now a viewport-fill QWidget child (resolved prior placeholder
  note). Real camera integration can replace the mock QImage source unchanged.

- mission_committed flag in SystemState gates MISSION_PARAMS_NOT_SET.
  Set True in _on_mission_config_committed(). Never reset to False in current
  implementation — once committed, stays committed. Future: reset on full
  mission reset if that feature is added. _compute_banner_status() returns
  UAV_TOO_FAR (not MISSION_PARAMS_NOT_SET) when mission_committed=True but
  guidance_result/vehicle_state/target_position not yet populated (covers the
  15–20s BackgroundPlannerLoop warm-up window).

---

## 12. Operator Workflow — How SCYTHE Is Used

1. UAV in flight or on ground, params not set
2. System shows NO_DROP + reason code
3. If MISSION_PARAMS_NOT_SET: operator clicks banner, routed to Mission Config tab
4. Operator sets: payload category, mass, Cd, area, target coords,
   evaluation depth, decision doctrine, simulation fidelity
5. Operator clicks "Commit Configuration"
6. System auto-redirects to Tactical Map tab
7. BackgroundPlannerLoop warms MC engine
8. Operator spots target on camera, clicks crosshair
9. Pixel converts to GPS coordinate
10. Auto-triggers simulation
11. Advisory output in <50ms hard target (70ms minimum acceptable)
12. Banner: NO_DROP -> APPROACH_CORRIDOR -> IN_DROP_ZONE -> DROP_NOW

Scope: static and slow-moving targets only.
NOT autonomous. Operator always in the loop.
Moving target support is next major upgrade after Phase 1.

---

## 13. Immutable Rules for Every Task

1.  PySide6 only. If you see PyQt5 anywhere, stop and flag it immediately.
2.  Never add to QGraphicsScene what belongs on the viewport.
3.  Never use self.repaint() — always self.update()
4.  Never hardcode physics values — all come from MotionPredictor
5.  Never change MapTransform, zoom logic, or pan logic unless explicitly tasked
6.  Run python -m py_compile on every modified file before reporting done
7.  Output change summary: every file modified, what changed, in which method
8.  Never refactor what is not broken
9.  Output complete files only when explicitly asked — changed blocks otherwise
10. One phase at a time — do not implement future phases speculatively
11. When in doubt: stop, list options with tradeoffs, do not proceed unilaterally
12. Never remove or alter math in Section 7 unless a physics upgrade was
    explicitly completed and verified by the supervising engineer

---

## 14. Autonomous Execution Ruleset — Phases 1.2 through 4.4

Read CLAUDE.md completely at the start of every session.
These rules override everything else. No exceptions.

=== AUTONOMOUS EXECUTION RULESET — PHASES 1.2 THROUGH 4.4 ===

RULE 0 — READ BEFORE WRITE
Read every file you will touch COMPLETELY before writing one line.
Never edit from memory. Never assume a method signature.
If a file is longer than 300 lines, read it in chunks and confirm
you have seen the relevant section before editing it.

RULE 1 — COMPILE AFTER EVERY SINGLE FILE CHANGE
python -m py_compile <file> after every modification.
Not after every phase. After every file. Every time.
If compile fails: stop, fix the syntax error, recompile.
Never proceed to the next file with a compile failure open.

RULE 2 — ONE FILE PER LOGICAL STEP
Never make multi-file atomic edits. Change one file, compile,
output the change summary, then move to the next file.
The only exception is CLAUDE.md -- it is always last.

RULE 3 — NO SPECULATIVE IMPLEMENTATION
Do not implement Phase 1.3 while working on Phase 1.2.
Do not add features not in the current phase spec.
Do not refactor code not targeted by the current phase.
If you see something broken that is outside scope: add it
to CLAUDE.md Section 11 Known Issues and leave it alone.

RULE 4 — SIGNAL/SLOT THREADING CONTRACT
All backend data flows to UI via Qt Signal(type).
Never call a UI method directly from a background thread.
Never bypass threading.Lock on SystemState.
Before wiring any new data path, trace: source → Signal → slot → UI method.
Write that trace as a comment above the connection line.

RULE 5 — MATH RULES
Before writing any numpy math:
  a. Write the formula from CLAUDE.md Section 7 as a comment
  b. Write the numpy code
  c. Write a trivial-case mental verification as a comment
For eigenvalue operations: eigh() returns ascending order.
  eigenvalues[1] = major axis (larger), eigenvalues[0] = minor.
For all sqrt(): guard with max(0.0, value) before sqrt.
For all matrix inverses: add 1e-9 * eye jitter before inverting.
Never use np.linalg.inv() -- use np.linalg.solve() or eigh().
Never change a formula in Section 7 without flagging it explicitly.

RULE 6 — NEVER TOUCH THESE WITHOUT EXPLICIT INSTRUCTION
  - MapTransform (any file, any method)
  - Zoom and pan logic
  - TelemetryLoop, GuidanceLoop frequencies
  - SystemState threading.Lock
  - RK2 integrator in monte_carlo.py
  - propagate_unscented() return contract (2,) and (2,2)
  - _compute_p_hit_heuristic_DEPRECATED (do not delete yet)

RULE 7 — PYSIDE6 ONLY
If you see any PyQt5 import: stop, flag it, do not proceed.
All Qt imports: from PySide6.QtX import Y. No exceptions.

RULE 8 — NO PRINT STATEMENTS
Use the existing logger. Search for existing logger instance
before adding logging. Never add a bare print() to any file
that does not already use print() as its logging mechanism.

RULE 9 — CLAUDE.md SELF-UPDATE IS MANDATORY
After every completed phase:
  - Mark completed items [x] in Section 9
  - Move CURRENT TASK label to next phase
  - Remove fixed items from Section 11
  - Add new Known Issues discovered during the task
  - Add architectural decisions made (method names, file locations)
  Commit message: "chore: update CLAUDE.md post-[phase-name]"

RULE 10 — WHEN IN DOUBT: STOP AND DOCUMENT
If you encounter ambiguity, a missing dependency, a shape mismatch,
or a design decision not covered by CLAUDE.md:
  STOP. Do not guess. Do not invent.
  Add a section to CLAUDE.md titled "BLOCKED — [phase] — [reason]"
  describing exactly what is missing and what options exist.
  Then halt and wait.

=== PHASE SPECS — EXECUTE IN ORDER ===

--- PHASE 1.2 — Guidance Arrow ---
Goal: Wire GuidanceResult.guidance_vector to GuidanceArrow UI layer.
Backend: corridor_guidance.py already produces GuidanceResult with
  guidance_vector (direction to fly), heading_error, distance_to_corridor.
UI: GuidanceArrow layer already instantiated in TacticalMapWidget.
Steps:
  1. Read corridor_guidance.py -- find GuidanceResult fields exactly
  2. Read TacticalMapWidget -- find update_guidance_arrow() signature
  3. Read tactical_map_controller.py -- find where corridor update happens
  4. Wire: after corridor update, call update_guidance_arrow() with
     guidance_vector and heading_error from GuidanceResult
  5. Arrow must point in ENU direction -- confirm Y-flip is applied
     consistently with MapTransform (guidance vector is in ENU,
     Qt scene is Y-flipped -- check existing MapTransform usage)
  6. Compile verify all touched files
  7. Update CLAUDE.md

--- PHASE 1.3 — Impact Ellipses Live Update ---
Goal: Display 68% and 95% confidence ellipses from UT covariance.
Backend: propagate_unscented() returns (2,) mean and (2,2) covariance.
Phase B already wired np.linalg.eigh() → semi-axes a/b/angle.
Confirm Phase B wiring is reaching update_impact_ellipse() with real data.
Steps:
  1. Read ImpactEllipseLayer -- find what update_impact_ellipse() expects
  2. Confirm the eigh() assignment: a=sqrt(eigenvalues[1]) major,
     b=sqrt(eigenvalues[0]) minor -- verify this is what Phase B wrote
  3. 68% ellipse: scale factor = 1.0 * semi-axes (1-sigma)
     95% ellipse: scale factor = 2.448 * semi-axes (chi2 2-DOF, p=0.95)
     chi2.ppf(0.95, df=2) = 5.991, sqrt(5.991) = 2.448
     Add these as named constants: SIGMA_68 = 1.0, SIGMA_95 = 2.448
  4. 68% ellipse color: #00AA44 (green), alpha 120
     95% ellipse color: #FF8C00 (amber), alpha 80
  5. Ellipses must NOT be scene items that transform with zoom/pan --
     confirm they are drawn in pixel space or recalculated on zoom
     (check how existing layers handle this -- match the pattern)
  6. Compile verify
  7. Update CLAUDE.md

--- PHASE 1.4 — Opportunity Corridor Visualization ---
Goal: Draw release corridor polygon on tactical map.
Backend: ReleaseEnvelopeResult.feasible_offsets already computed.
CorridorLayer already instantiated in TacticalMapWidget.
Steps:
  1. Read release_envelope_solver.py -- find feasible_offsets structure
     (what units, what coordinate frame, what shape)
  2. Read CorridorLayer -- find update_corridor() signature exactly
  3. Convert feasible_offsets to 4-point polygon in ENU coordinates:
     corridor is a rectangle along vehicle heading:
       entry point = vehicle_pos + heading_vector * range_min
       exit point  = vehicle_pos + heading_vector * range_max
       width = lateral_offset left and right of heading
     All in ENU meters -- MapTransform handles pixel conversion
  4. Corridor fill: #00AA44 alpha 40 (green, semi-transparent)
     Corridor border: #00AA44 alpha 180, line width 1px
  5. Compile verify
  6. Update CLAUDE.md

--- PHASE 2.1 — DEM Flat Placeholder ---
CORRECTION: wire into build_propagation_context, not MotionPredictor.
(MotionPredictor has no impact detection; §7.8 lives in monte_carlo.py
which Rule 6 forbids touching. Injection point is PropagationContext.target_z,
set at the runtime call site in BackgroundPlannerLoop.tick.)
TerrainModel instance is constructed in product/runtime/run_scythe.py
(the actual loop bootstrap) and passed to BackgroundPlannerLoop, not in
qt_app/main_window.py which has no runtime-loop wiring.
Goal: Introduce georeferenced ground elevation model (flat for now).
Steps:
  1. Create product/terrain/terrain_model.py
     Class: TerrainModel
     Method: get_elevation(x_enu, y_enu) -> float
     Implementation: return 0.0 (flat placeholder)
     Method: is_loaded() -> bool, returns False until real DEM loaded
  2. Wire TerrainModel into MotionPredictor:
     Add terrain: TerrainModel parameter to __init__
     Replace hardcoded z_ground=0.0 with terrain.get_elevation(x, y)
     in the impact detection interpolation (CLAUDE.md §7.8)
  3. Default: TerrainModel() instance created in main_window.py,
     passed into MotionPredictor at construction
  4. Do not touch the physics math -- only replace the z_ground source
  5. Compile verify all touched files
  6. Update CLAUDE.md

--- PHASE 2.2 and 2.3 — SRTM Loader and DEM Integration ---
STOP before implementing these.
Add to CLAUDE.md Section 11:
"Phase 2.2 SRTM loader requires network access and file I/O design
decision. Halt and document options before implementing."
Then halt. These require supervisor review before proceeding.

--- PHASE 3.1 — Camera Feed Background ---
Goal: Display camera feed as background layer in TacticalMapWidget.
Implementation: Option A only (raw overlay, no georeferencing).
Steps:
  1. Camera feed source: check if mock or real feed exists in codebase.
     Search for camera, capture, VideoCapture, feed in all files.
     Document what exists before writing anything.
  2. If no feed source exists: create a mock feed generator that
     produces a solid dark gray QImage (placeholder only).
     File: product/sensors/camera_feed.py
     Class: CameraFeed, method: get_frame() -> QImage or None
  3. Feed renders as bottom-most layer in the scene (z-order 0).
     All other layers render above it.
  4. Feed updates at UIRenderLoop frequency (30Hz) -- not its own loop.
  5. No georeferencing. No coordinate mapping. Raw pixel display only.
  6. If QImage cannot be obtained: layer shows nothing (silent fail,
     no exception, no crash).
  7. Compile verify
  8. Update CLAUDE.md

--- PHASE 4.1 — Wind Indicator on Map ---
Goal: Visual wind direction and speed indicator on tactical map.
Data source: TelemetryLoop already ingests wind_direction and wind_speed.
Steps:
  1. Find where wind data is stored in SystemState or TelemetryLoop output
  2. Create a WindIndicatorLayer or use existing WindFieldLayer --
     check if WindFieldLayer exists and what it expects
  3. Indicator: arrow at fixed screen position (bottom-left of viewport,
     not scene position -- viewport child or recalculated on zoom)
     Arrow direction: downwind direction in ENU, Y-flipped for Qt
     Arrow length: proportional to wind speed, clamped 20-80px
     Label: "{speed:.1f} m/s" below arrow, white 9pt
  4. Update at UIRenderLoop frequency
  5. Compile verify
  6. Update CLAUDE.md

--- PHASE 4.2 — CEP50 Overlay on Map ---
Goal: Display CEP50 as text overlay near impact ellipse.
Formula (§7.13): CEP50 = 0.8326 * sqrt(lambda1 + lambda2)
  where lambda1, lambda2 are eigenvalues of impact covariance
  (eigenvalues directly, NOT semi-axes).
Steps:
  1. In tactical_map_controller.py after eigvals are computed:
     cep50_m = 0.8326 * math.sqrt(float(eigvals[0]) + float(eigvals[1]))
  2. Pass cep50_m to new TacticalMapWidget.update_cep_label(cep_m: float)
  3. Create CepLabelLayer in tactical_map_widget.py:
     - QGraphicsTextItem (scene item, not viewport child)
     - Text: "CEP₅₀: {cep_m:.1f} m"
     - Font: 9pt monospace, color #00AA44
     - Position: 10px below 68% ellipse center in scene coords
     - setZValue(50)
  4. If cep_m > 999.9: display "CEP₅₀: ---"
  5. Compile verify, update CLAUDE.md

--- PHASE 4.3 — P_hit Deprecated Function Cleanup ---
Goal: Verify MC replacement works, then delete deprecated function.
Steps:
  1. Read release_time_explorer.py, find _compute_p_hit_heuristic_DEPRECATED
     and every call site of _compute_p_hit_mc
  2. Confirm _compute_p_hit_heuristic_DEPRECATED is called NOWHERE
  3. If called nowhere: delete it entirely (no replacement, no comment)
  4. If called somewhere: HALT per Rule 10, document where
  5. Search entire codebase for DEPRECATED keyword to catch others
  6. Compile verify release_time_explorer.py
  7. Update CLAUDE.md §11: remove the deprecated deletion reminder

--- PHASE 4.4 — Window Title and Version String Final Fix ---
Goal: Confirm "SCYTHE" appears everywhere, no AIRDROP-X branding.
Steps:
  1. grep -ri "airdrop" across project excluding: .venv, __pycache__,
     .git, CLAUDE.md history sections, filesystem path E:/AIRDROP-X
     (that is a path, not branding -- do not change)
  2. For each hit: branding vs filesystem path assessment
  3. Fix branding references only
  4. Confirm system_status tab shows "SCYTHE v1.1" not "AIRDROP-X"
  5. Compile verify
  6. Update CLAUDE.md §11

--- PHASE 4.5 — Wind-Component Correlation in UT Covariance ---
Goal: Add off-diagonal wind correlation terms to UT state model.
Diagonal covariance produces overconfident ellipses.

STOP before implementing. Read these files completely first:
  product/uncertainty/unscented_state_model.py
  product/uncertainty/unscented_sigma_points.py

Output:
  1. Exact lines where Sigma[0,0] and Sigma[1,1] are set
  2. Current values of wind_x and wind_y variance
  3. Whether a heading_uncertainty parameter exists anywhere
  4. Full 5x5 covariance matrix as currently constructed

HALT after outputting. Do not implement. Supervisor math review required.
Add findings to CLAUDE.md §11 and halt.

=== END OF AUTONOMOUS RULESET ===

---

## 15. Self-Update Instructions

At the end of every completed task, update this file as follows:

1. Section 9 — Phase Completion Status:
   - Mark newly completed items [x]
   - Move CURRENT TASK label to next phase
   - Add architectural decisions made during the task

2. Section 11 — Known Issues:
   - Remove issues that were fixed
   - Add new issues discovered

3. Other sections:
   - Update facts that changed (class names, file paths, method names)
   - Do NOT rewrite sections you did not touch
   - Do NOT change Section 7 math unless a physics upgrade was completed

4. Commit message format: "chore: update CLAUDE.md post-[phase-name]"
