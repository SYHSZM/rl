# Stage 1 Task 5 Formal Matrix Execution Plan

> **Execution mode:** Inline execution in the current authorized workspace. The task explicitly forbids production changes, worktrees, and commits; every created or modified file remains under `outputs/stage_1/`.

**Goal:** Execute the frozen 15-run scene stability matrix exactly once per valid identity, resume safely after interruption, and seal machine-readable quality evidence without interpreting traffic-state boundaries.

**Architecture:** `run_scene_matrix.py` owns immutable matrix constants, writes and verifies a frozen plan before SUMO execution, invokes the accepted CR-04 `scan_demand` API once for each fixed low/medium/high configuration, then rebuilds consolidated evidence only from on-disk scan manifests and attempt artifacts. Validation is fail-closed: an infrastructure, identity, artifact, log, window, detector, configuration, or network mismatch stops the script with a nonzero exit and no parameter adjustment.

**Tech stack:** Python standard library, accepted project CR-03/CR-04 APIs, SUMO 1.25.0, Pillow for a static PNG time-series overview.

## Global Constraints

- Matrix is exactly low `3000/300`, medium `4500/600`, high `6000/1200`; controller `none`; seeds `0,1,2,3,4`.
- Every run is exactly 3600 s, 1 s steps, 30 s windows, with phases `0–600 ×0.65`, `600–2400 ×1.00`, `2400–3600 ×0.70`.
- No production code, tests, docs, or network source file may be changed.
- All new execution code and evidence must be under `outputs/stage_1/`.
- Do not classify or explain free-flow, critical, or congested boundaries.
- Set `SUMO_HOME=D:\sumo-1.25.0`; stop on infrastructure defects; do not commit.

### Task 1: Freeze the execution plan and validate infrastructure

**Files:**
- Create: `outputs/stage_1/run_scene_matrix.py`
- Create/update: `outputs/stage_1/frozen_matrix_plan.json`

- [ ] Encode the exact matrix, timing, phases, expected schemas, required eight detector IDs, and output paths as immutable constants.
- [ ] Implement atomic JSON/CSV writes and SHA-256 helpers.
- [ ] Implement `--plan-only` to verify SUMO, project imports, source files, frozen plan identity, and source hashes without running simulations.
- [ ] Run `--plan-only`; require exit code 0 and exactly 15 planned identities.

### Task 2: Execute or resume the formal CR-04 scans

**Files:**
- Create/update: `outputs/stage_1/formal_scans/stage1_{low,medium,high}_formal_v1/**`
- Create/update: `outputs/stage_1/matrix_execution.log`

- [ ] Build one exact `ExperimentConfig` per level with the five frozen seeds.
- [ ] Invoke `scan_demand(..., controllers=("none",), resume=True, scan_id=...)` in low, medium, high order without modifying any constant after execution begins.
- [ ] Flush progress before and after each level so interruption state is observable and the same command can resume valid attempts.
- [ ] Stop immediately if CR-04 raises an infrastructure or frozen-network exception.

### Task 3: Rebuild strict quality evidence from disk

**Files:**
- Create/update: `outputs/stage_1/run_index.csv`
- Create/update: `outputs/stage_1/quality_summary.csv`
- Create/update: `outputs/stage_1/scene_timeseries_data.csv`
- Create/update: `outputs/stage_1/scene_matrix_manifest.json`

- [ ] Verify each scan manifest identity, full configuration, protocol/schema values, frozen net hash, and source hashes.
- [ ] Verify exactly one selected valid attempt for every one of the 15 planned runs while preserving every attempt row in the consolidated index.
- [ ] Recompute artifact inventory size/hash equality, exact config and network snapshots, attempt success/valid state, one summary row, 120 exact window times, eight native detector XML files, tripinfo/log presence, and no missing outputs.
- [ ] Scan logs for collision, teleport, emergency-braking, and SUMO error evidence; require zero summary/window teleports and an empty `sumo_error.log`.
- [ ] Rehash all five network source files after execution and require equality with the frozen pre-run hashes.
- [ ] Atomically write a final manifest that records all 15 selected attempts, scan and artifact hashes, validation counts, and source hashes before/after.

### Task 4: Generate review plots and final report

**Files:**
- Create/update: `outputs/stage_1/scene_timeseries_overview.png`
- Create/update: `outputs/stage_1/task-report.md`

- [ ] Aggregate each level/time point across the five seeds without dropping raw per-seed evidence.
- [ ] Draw aligned low/medium/high panels for mean speed, bottleneck flow, ramp maximum queue, and actual departure ratio, with frozen phase boundaries at 600 s and 2400 s.
- [ ] Write the required four-part task report: files changed, outputs produced, commands/raw results, remaining risks/questions; do not interpret demand boundaries.
- [ ] Verify PNG readability, manifest/CSV parseability, 15/15 validity, 120 windows each, eight native XML files each, zero integrity/log defects, and no writes outside `outputs/stage_1/`.
