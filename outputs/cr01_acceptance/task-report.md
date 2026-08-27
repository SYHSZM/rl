# Task 3 / CR-01 acceptance report

Status: CR-01 correction round 1/5 implementation and local acceptance are complete. Accepted CR-05 behavior remains in place; CR-02 through CR-04 were not started. This evidence is a software/metric audit and does not state traffic-study conclusions.

## 1. Modified files

Tracked production and test changes are exactly:

- `env.py`
- `metrics.py`
- `tests/test_env_runner.py`
- `tests/test_metrics.py`

All new reports, commands, logs, isolated test material, and runtime attachments are under `outputs/cr01_acceptance/`. No changes were made to `build_network.py`, `scan_demand.py`, `classify.py`, docs, network XML, prior evidence, or unrelated tests. Commit: `none`.

## 2. Implementation

- `run_experiment` now takes one online detector sample immediately after every 1-second `simulationStep()` and aggregates each complete 30-second `[a,b)` window. Controller occupancy/queue sampling and update timing are unchanged.
- Mainline and upstream speeds use E1 step count weighting, excluding observations where count is zero or speed is negative. A zero denominator produces `None`, which CSV writes as blank.
- Bottleneck flow is the sum of step counts; occupancy is the complete-window time mean of fractional occupancy.
- Ramp E2 output contains explicit vehicle mean/max and halting mean/max. `ramp_queue_veh` is retained only as a compatibility alias equal to `ramp_vehicle_max_veh`; episode max formally reads the explicit max field.
- Window rows carry `metric_schema_version=cr01-window-v1`, `estimator_id=traci-1s-step-complete-window`, step count, and main/upstream speed vehicle-observation counts.
- Episode summary averages only present main speeds. An episode with no main-speed observation is invalid with `no_main_speed_observations`; one with no upstream-speed observation is invalid with `no_upstream_speed_observations`. `classify_episode` safely returns `invalid` without changes to `classify.py`.
- Missing/non-low upstream windows terminate a low-speed run. Breakdown starts at the first run meeting the minimum duration, and congestion duration sums only qualified contiguous runs; isolated short low runs do not contribute.
- Run-local native E1/E2 XML remains the parallel audit attachment; native interval values do not replace the online step estimator.

## 3. Commands and exact results

All Python commands used bundled Python 3.12 with `SUMO_HOME=D:\sumo-1.25.0` and `D:\sumo-1.25.0\bin` on `PATH`.

Initial CR-01 RED, before production changes:

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr01_acceptance\red_tmp
```

Result: exit 1, `4 failed, 11 passed in 5.85s`. The expected failures were the missing 30-step accumulator/schema, missing speed-observation CSV fields, and old summary failure on `None`. Evidence: `red_focused_*`.

Schema-version TDD subcycle RED:

```text
python -m pytest tests/test_env_runner.py::test_short_sumo_run_produces_windows -q
```

Result: exit 1, `1 failed in 1.15s`, specifically missing `metric_schema_version`. Evidence: `red_schema_version_*`.

Final CR-01 focused GREEN:

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr01_acceptance\green_final_tmp
```

Result: exit 0, `15 passed in 5.74s`. Evidence: `green_focused_final_*`.

CR-05 focused regression:

```text
python -m pytest tests/test_build_network.py tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr01_acceptance\cr05_regression_tmp
```

Result: exit 0, `21 passed in 6.29s`. Evidence: `cr05_regression_*`.

Isolated full suite, run from `outputs/cr01_acceptance/pytest_sandbox/project/`:

```text
python -m pytest -q --basetemp ..\full_tmp
```

Result: exit 0, `35 passed in 6.34s`. The real workspace before/after guard reports `unchanged=true`, `changed_paths=[]`. Evidence: `full_pytest_isolated_*`, `pytest_sandbox_copy_manifest.json`, and `full_pytest_real_workspace_hash_guard.json`.

Required 360-second audit:

```text
python outputs\cr01_acceptance\audit_360_runner.py
```

Result: exit 0, valid run with 12 windows and 8 native XML files. First window: main speed `33.1209244658188`, main vehicle observations `2`, upstream speed blank with zero observations, bottleneck flow `0`, occupancy `0.0`, ramp vehicle mean `0.4666666666666667`, max `1`. These match the Task 1 hand calculation. Legacy TTS remains exactly `19530.0 s`; real `network/*.xml` hashes are unchanged. Evidence: `audit_360_run_*`, `audit_360_check.json`, and `real_workspace_network_hash_guard.json`.

Git scope checks:

- `git diff --name-status` lists exactly the four allowed tracked files.
- `git diff --check` exits 0 with no whitespace errors; stderr contains only Git LF-to-CRLF working-tree notices.
- Evidence: `git_diff_name_status_*` and `git_diff_check_*`.

Correction round 1/5 RED and GREEN:

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr01_acceptance\correction1_red_tmp
```

RED result: exit 1, `4 failed, 14 passed in 6.05s`. Failures proved the old whole-episode low-window sum and both missing-episode-speed validity defects. The rewritten empty-window test already passed through 30 real accumulator samples and CSV output. Evidence: `correction1_red_*`.

The first correction GREEN attempt produced `2 failed, 16 passed in 5.97s`: two CR-05 tests used a 30-second scenario and still required `valid=True`, but that complete episode has no upstream observation under the corrected metric. Their scenarios were extended to the already-established 180-second integration duration so they continue testing network reuse/output isolation without contradicting the new validity rule. Evidence: `correction1_green_*`.

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr01_acceptance\correction1_green_final_tmp
```

Final correction GREEN: exit 0, `18 passed in 6.71s`. Correction CR-05 regression: `22 passed in 6.97s`. Correction isolated full suite: `38 passed in 7.12s`, with real-workspace guard `unchanged=true`, `changed_paths=[]`. The repeated 360-second audit passed with the same first-window values and legacy TTS `19530.0 s`; network hashes remained unchanged. Evidence prefixes: `correction1_green_final_*`, `correction1_cr05_regression_*`, `correction1_full_pytest_isolated_*`, and `correction1_audit_360_*`.

## 4. Remaining risks and limits

- Online one-second E1 aggregation and native E1 interval XML have intentionally different boundary/population semantics; native files remain authoritative parallel audit evidence, not silent substitutes.
- Occupancy weighting is an arithmetic mean because the approved configuration uses uniform 1-second steps. A future variable-step protocol would need explicit duration weights.
- All-main or all-upstream missing episodes are now explicitly invalid; the broader policy for partially observed episodes remains limited to the approved CR-01 rules.
- Legacy right-endpoint TTS is deliberately unchanged and remains blocked for research use until CR-02. Failure-stage schema and resume behavior remain deferred to CR-03 and CR-04.
