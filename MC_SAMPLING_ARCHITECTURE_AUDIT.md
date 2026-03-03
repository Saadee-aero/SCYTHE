# Monte Carlo Sampling Architecture — Full Structural Audit

**Date:** 2025-02-22  
**Scope:** All definitions and uses of `n_samples`, `N`, `sample_count`, and default sample values.  
**Constraint:** Audit only — no code modifications.

---

## 1. DEFINITIONS AND SOURCES

### 1.1 Default / Canonical Definitions

| Location | Symbol | Value | Purpose |
|----------|--------|-------|---------|
| `configs/mission_configs.py:29` | `n_samples` | 300 | Global config default |
| `qt_app/main_window.py:54` | `_mission_config_overrides["n_samples"]` | 1000 | Initial app override |
| `qt_app/main_window.py:139` | `build_config_snapshot()` | 1000 | CONFIG snapshot template |
| `qt_app/mission_config_tab.py:50` | `N_SAMPLES_PRESETS` | (300, 500, 1000, 1500) | Preset buttons |
| `qt_app/mission_config_tab.py:135` | `_n_samples` | 1000 | MissionConfigTab default |
| `qt_app/mission_config_tab.py:473-475` | `_n_samples_spin` | range 30–10000, default 1000 | UI spinbox |
| `qt_app/side_panel.py:124-125` | `num_samples` | range 50–1000, step 50 | Left panel (hidden) |
| `product/analysis/variance_decomposition.py:30` | `N` (kwarg) | 500 | Variance decomposition runs |
| `src/decision_doctrine.py:15` | `MIN_VALID_N` | 30 | Minimum valid sample count |
| `product/guidance/numerical_diagnostics.py:14` | `samples` (param) | 5 | Stability check |

### 1.2 Fallback Defaults (1000 vs 300)

| Location | Fallback | Context |
|----------|----------|---------|
| `main_window.py:259` | 1000 | `config_state.data.get("n_samples", 1000)` |
| `main_window.py:686` | 1000 | `cfg.get("n_samples", 1000)` |
| `main_window.py:706` | 1000 | `_latest_snapshot.get("n_samples", 1000)` |
| `main_window.py:849` | 1000 | `snapshot.get("n_samples", 1000)` |
| `main_window.py:943` | 1000 | `snapshot.get("n_samples", 0) or n_samples or 0` |
| `main_window.py:1779` | 1000 | `_mission_config_overrides.get("n_samples", 1000)` |
| `main_window.py:1797` | 1000 | `cfg.get("n_samples", 1000)` |
| `main_window.py:1831` | 300 | `data.get("n_samples", 300)` in `_handle_evaluation_result` |

---

## 2. DATA FLOW PATH

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 1. CONFIG SOURCES                                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│  configs/mission_configs.py  n_samples = 300  (never used as primary source)     │
│  _mission_config_overrides   n_samples = 1000 (main_window init)                 │
│  MissionConfigTab            _n_samples_spin.value() → on commit                 │
│  side_panel (HIDDEN)         num_samples.value() → get_config_values()          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 2. CONFIG STATE                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│  _seed_config_state()  →  config_state.data["n_samples"] = _mission_config_     │
│                           overrides.get("n_samples", 1000)                      │
│  _push_config_to_worker() → cfg["n_samples"] = int(cfg.get("n_samples", 1000))  │
│  _on_mission_config_committed(cfg) → _mission_config_overrides = committed_cfg  │
│  mission_config_tab.get_config() → n_samples: self._n_samples_spin.value()      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 3. SIMULATION WORKER                                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│  _start_simulation() → cfg = config_state.data (after _push_config_to_worker)   │
│  SimulationWorker(cfg, trigger) → run_simulation_snapshot(config_override=cfg)  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 4. ADAPTER                                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│  run_simulation_snapshot(config_override)                                        │
│    n_samples = int(overrides.get("n_samples", cfg.n_samples))  [line 126]       │
│    saved_n_samples = cfg.n_samples; cfg.n_samples = n_samples  [lines 145–146]  │
│    get_impact_points_and_metrics(mission_state, random_seed)                     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 5. ADVISORY LAYER                                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│  get_impact_points_and_metrics(mission_state, random_seed)                       │
│    Temporarily mutates cfg with engine_inputs (uav_pos, wind_mean, etc.)        │
│    n_samples comes from cfg.n_samples (set by adapter before call)              │
│    run_monte_carlo(..., cfg.n_samples, random_seed, ...)  [line 63]             │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 6. MONTE CARLO                                                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│  run_monte_carlo(pos0, vel0, ..., n_samples, random_seed, ...)                  │
│    _draw_wind_batch(rng, wind_mean, wind_std, n_samples)                        │
│    sensor_model.perturb_batch(pos0, vel0, n_samples, rng)                       │
│    generate_wind_drift_batch(rng, n_samples, ...)  [if time_varying_wind]       │
│    _propagate_payload_batch(...)  [N = wind_samples.shape[0]]                   │
│    impact_array = result[0].reshape(n_samples, 2)                               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. CALL GRAPH FOR run_monte_carlo

```
run_monte_carlo(n_samples, ...)
  ├── sensor_model.perturb_batch(pos0, vel0, n_samples, rng)     [if sensor_model]
  ├── generate_wind_drift_batch(rng, n_samples, ...)             [if time_varying_wind]
  ├── _draw_wind_batch(rng, wind_mean, wind_std, n_samples)
  ├── generate_correlated_wind_profiles_batch(...)               [wind_ref_batch shape (N,K,3)]
  └── _propagate_payload_batch(pos0, vel0, ..., wind_samples)
        N = wind_samples.shape[0]  (derived from n_samples)
```

**Direct callers of `run_monte_carlo`:**

| Caller | File | n_samples source |
|--------|------|------------------|
| `get_impact_points_and_metrics` | `product/guidance/advisory_layer.py:63` | `cfg.n_samples` (set by adapter) |
| `quick_stability_check` | `product/guidance/numerical_diagnostics.py:29,43` | Param `samples=5` |
| `compute_uncertainty_contributions` | `product/analysis/variance_decomposition.py:100` | Param `N=500` (fixed) |
| Phase 1–10 validation | `validate_monte_carlo.py` | Hardcoded (50, 30, 300, 200, 100, 300) |

---

## 4. FUNCTIONS THAT DEPEND ON n_samples

| Function | File | Dependency type |
|----------|------|-----------------|
| `_draw_wind_batch` | `src/monte_carlo.py` | Param `n_samples` → shape `(n_samples, 3)` |
| `perturb_batch` | `product/uncertainty/sensor_model.py` | Param `n_samples` → `pos_batch`, `vel_batch` |
| `generate_wind_drift_batch` | `product/physics/wind_model.py` | Param `n_samples` → `(N, 3)` arrays |
| `generate_correlated_wind_profiles_batch` | `product/physics/wind_model.py` | Implicit via `wind_ref_batch.shape[0]` |
| `_propagate_payload_batch` | `src/monte_carlo.py` | Implicit via `wind_samples.shape[0]` |
| `run_monte_carlo` | `src/monte_carlo.py` | Param `n_samples` — primary entry |
| `get_impact_points_and_metrics` | `product/guidance/advisory_layer.py` | Via `cfg.n_samples` |
| `run_simulation_snapshot` | `qt_app/adapter.py` | `overrides.get("n_samples", cfg.n_samples)` |
| `compute_uncertainty_contributions` | `product/analysis/variance_decomposition.py` | Param `N=500` (fixed) |
| `evaluate_doctrine` | `src/decision_doctrine.py` | Param `n_samples` (from snapshot) |
| `compute_wilson_ci` | `src/statistics.py` | Param `n` (hits, total) |

---

## 5. DUPLICATED SAMPLING LOGIC

| Duplication | Description | Risk |
|-------------|-------------|------|
| **Two UI controls for n_samples** | `MissionConfigTab._n_samples_spin` (visible) and `side_panel.num_samples` (hidden) | Left panel is hidden but still wired; Analysis/System tabs read `left_panel.num_samples.value()` — potential mismatch with MissionConfigTab |
| **cfg.n_samples mutation** | Adapter temporarily sets `cfg.n_samples = n_samples` before advisory call | Correct pattern; advisory reads from mutated cfg |
| **n_actual vs n_samples** | Adapter computes `n_actual = impact_arr.shape[0]` and uses it for Wilson CI, doctrine; snapshot stores `n_actual` | Redundant but correct (impact count equals requested n_samples unless error) |
| **1000 vs 300 defaults** | Most fallbacks use 1000; `mission_configs` and `_handle_evaluation_result` use 300 | Inconsistent; evaluation result handler defaults to 300 |

---

## 6. ADVANCED vs STANDARD MODE — n_samples

- **No conditional N by mode.** Both standard and advanced use the same `n_samples` from config.
- **Variance decomposition** uses fixed `N=500` (separate from main MC run) — not mode-dependent.

---

## 7. VALIDATION SCRIPT HARDCODED N

| Phase | N | Purpose |
|-------|---|---------|
| Phase 1 | 50 | Determinism check |
| Phase 2 | 50 | RK2 convergence |
| Phase 3 | 30 | Edge cases (all 7) |
| Phase 5 | 1000, 2000 | Performance benchmarks |
| Phase 6 | 300 | dt sweep |
| Phase 7 | 200 | Sensor noise |
| Phase 8 | 300 | Release jitter |
| Phase 9 | 300, 100 | Variance decomposition |
| Phase 10 | 300 | Time-varying wind |

**None of these use config or UI values** — all are local script constants.

---

## 8. POTENTIAL RISKS

### 8.1 Double Overrides

- **Risk:** `config_state.data` is seeded from `_mission_config_overrides`, then `_push_config_to_worker` merges `_mission_config_overrides` again. Flow is consistent but indirect.
- **Recommendation:** Document that `_mission_config_overrides` is the source of truth until MissionConfigTab commit, then it is replaced by committed config.

### 8.2 Legacy Code Paths

- **Left panel (side_panel) is hidden** but `main_window` still:
  - Reads `left_panel.num_samples.value()` for Analysis tab and System Status tab
  - Connects `left_panel.num_samples.valueChanged`
  - Loads defaults into `left_panel` via `_load_defaults()` (from `cfg.n_samples`)
- **Risk:** If left panel and MissionConfigTab are ever out of sync, Analysis/System tabs would show left_panel values, while simulation uses MissionConfigTab (via config_state after commit).

### 8.3 Conditional Branches with Fixed N

- **Variance decomposition:** Always uses `N=500` regardless of main run N.
- **Numerical diagnostics:** Uses `samples=5` — intentionally small for stability check.
- **Fragility:** Calls `run_simulation_snapshot` with config override (wind +0.5); inherits `n_samples` from config — no fixed N.

### 8.4 UI Coupling to N

| UI element | Source | Used for |
|------------|--------|----------|
| `sample_count_label` | `snapshot.get("n_samples")` | Display "Sample count: N" |
| `_render_analysis_tab` | `left_panel.num_samples.value()` | Passed to `analysis_tab_renderer.render(n_samples=...)` |
| `_render_system_tab` | `left_panel.num_samples.value()` | Passed to `system_status.render(n_samples=...)` |
| Mission overview | `n_samples` from `_render_mission_tab_operator` (from snapshot) | Display |

**Risk:** Analysis and System tabs read `left_panel.num_samples`, not snapshot or MissionConfigTab. After a simulation, snapshot has `n_samples` from adapter (`n_actual`), but Analysis/System tabs still show left_panel value.

---

## 9. REFACTOR RECOMMENDATIONS

### High priority

1. **Unify n_samples source for display**  
   Use `snapshot.get("n_samples")` (or config_state when no snapshot) for Analysis and System tab display, instead of `left_panel.num_samples.value()`.

2. **Resolve 1000 vs 300 default**  
   Choose one canonical default (e.g. 1000) and replace `data.get("n_samples", 300)` in `_handle_evaluation_result` with 1000 for consistency.

3. **Clarify left panel role**  
   Either:
   - Remove left_panel usage for n_samples and rely on config_state/MissionConfigTab, or
   - Keep left_panel as source and ensure it is synced from MissionConfigTab on commit.

### Medium priority

4. **Single source for n_samples in config**  
   Prefer `mission_configs.n_samples` (300) or a single app default (1000) and avoid multiple defaults in `main_window`.

5. **Document cfg.n_samples mutation**  
   Add a short comment in adapter that advisory reads `cfg.n_samples` after temporary override.

### Low priority

6. **Variance decomposition N**  
   Consider making `N` configurable or derived from main run (e.g. `min(500, n_samples)`) for consistency, while keeping a cap for performance.

7. **validation constants**  
   Consider a shared `validate_monte_carlo.N_PHASE_*` or similar for clarity, though not critical.

---

## 10. SAFE REMOVAL POINTS

| Item | Safe to remove? | Notes |
|------|-----------------|-------|
| `side_panel.num_samples` | No (yet) | Still used by `_render_analysis_tab` and `_render_system_tab`; would need to switch those to snapshot/config first |
| `configs/mission_configs.n_samples = 300` | No | Used as fallback in adapter when overrides lack `n_samples` |
| `build_config_snapshot` hardcoded 1000 | Conditional | Only if `config_state` is always seeded before use; otherwise keep as template default |
| Variance decomposition `N=500` | No | Fixed by design for performance; change would alter analysis behavior |

---

## 11. ARCHITECTURE MAP (SIMPLIFIED)

```
UI (MissionConfigTab)     →  get_config()["n_samples"]
        │
        ▼
config_state.data        ←  _mission_config_overrides (init)
        │                ←  MissionConfigTab commit
        ▼
_push_config_to_worker   →  cfg["n_samples"]
        │
        ▼
SimulationWorker(cfg)    →  run_simulation_snapshot(config_override=cfg)
        │
        ▼
adapter                  →  n_samples = overrides.get("n_samples", cfg.n_samples)
        │                →  cfg.n_samples = n_samples (temporary)
        ▼
get_impact_points_and_metrics  →  run_monte_carlo(..., cfg.n_samples, ...)
        │
        ▼
run_monte_carlo          →  All batch operations use n_samples
        │
        ▼
result                   →  snapshot["n_samples"] = n_actual
```

---

**End of audit.**
