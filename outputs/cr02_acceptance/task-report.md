# Task 3 / CR-02 acceptance report

Status: CR-02 implementation and local acceptance are complete. Accepted CR-05 and CR-01 behavior remains in place; CR-03 and CR-04 were not started. This is software/metric audit evidence, not a traffic-study conclusion.

## 1. Modified files

Tracked changes are exactly:

- `env.py`
- `metrics.py`
- `tests/test_env_runner.py`
- `tests/test_metrics.py`

Evidence and run-local audit output are under `outputs/cr02_acceptance/`. No changes were made to `rou_generate.py`, `classify.py`, `build_network.py`, `scan_demand.py`, docs, network XML, prior evidence, or unrelated tests. Commit: `none`.

## 2. Implementation

- After every `simulationStep()`, `_SystemExposureTracker` updates cumulative loaded/departed/arrived ID sets. Pending is `loaded_seen - departed_seen`; in-network is `departed_seen - arrived_seen`.
- Each 1-second step adds exposure to every current pending and in-network ID. Per-ID pending/in-network/system exposure is retained through the horizon without extrapolation.
- Generated `main_` and `ramp_` prefixes produce separate origin totals. Unknown prefixes remain `unknown`, appear in `unknown_ids`, and are never assigned to a known origin.
- Window schema is `cr02-window-v1` and adds cumulative loaded, current pending/in-network, departure/completion ratios, and cumulative system/pending/in-network TTS. Zero loaded denominator writes blank ratios.
- Episode `total_time_spent_s` is corrected system exposure. The exact prior 30-second right-endpoint proxy remains `legacy_total_time_spent_s`.
- Episode schema is `cr02-summary-v1` and includes counts, ratios, TTS components, origin exposures, completed exposure, terminal counts/IDs/exposures, and terminal censoring.
- Each successful attempt writes deterministic `system_exposure.json` with terminal lists and per-ID pending/in-network exposures. Raw tripinfo remains completed-only reconciliation evidence.
- Existing paired classification automatically reads corrected `total_time_spent_s`; `classify.py` was not changed.

## 3. Commands and exact results

All commands used bundled Python 3.12 with `SUMO_HOME=D:\sumo-1.25.0` and `D:\sumo-1.25.0\bin` on `PATH`.

Initial CR-02 RED:

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr02_acceptance\red_tmp
```

Result: exit 1, `6 failed, 16 passed in 6.56s`. Expected failures covered the missing six-step tracker, origin/unknown accounting, ratio/schema/JSON integration, corrected/legacy summary, and classifier use. Evidence: `red_focused_*`.

CR-02 GREEN:

```text
python -m pytest tests/test_metrics.py tests/test_env_runner.py -q --basetemp outputs\cr02_acceptance\green_final_tmp
```

Result: exit 0, `23 passed in 6.68s`. The synthetic trajectory exactly produced pending/in-network/system `3/7/10 s`, mainline/ramp/unknown `6/4/0 s`, counts `3/3/2`, ratios `1` and `2/3`, completed exposure `6 s`, and terminal `main_C` exposure `4 s`. Evidence: `green_focused_final_*`.

Regression:

- CR-01 focused command (`tests/test_metrics.py tests/test_env_runner.py`): `23 passed in 6.53s`; evidence `cr01_focused_regression_*`.
- CR-05 command (`tests/test_build_network.py tests/test_env_runner.py tests/test_scan_demand.py`): `24 passed in 7.04s`; evidence `cr05_regression_*`.
- Isolated complete suite: `43 passed in 7.38s`; evidence `full_pytest_isolated_*` and `pytest_sandbox_copy_manifest.json`.
- Isolated-suite real workspace guard: `unchanged=true`, `changed_paths=[]`; evidence `full_pytest_real_workspace_hash_guard.json`.

Frozen 360-second audit:

```text
python outputs\cr02_acceptance\audit_360_runner.py
```

Result: exit 0. Exact reconciliation:

- system/in-network/pending TTS: `18817/18817/0 s`;
- corrected `total_time_spent_s=18817 s`;
- legacy proxy: `19530 s`;
- completed tracker exposure and tripinfo duration: `11302/11302 s`;
- terminal in-network/pending counts: `73/0`;
- terminal in-network exposure: `7515 s`;
- completed plus terminal exposure: `18817 s`;
- loaded/departed/arrived: `132/132/59`;
- 12 windows, 8 native XML, CR-01 first-window values unchanged;
- real network hashes unchanged.

Evidence: `audit_360_*`, `audit_360_check.json`, `real_workspace_network_hash_guard.json`, and the run-local `system_exposure.json`/tripinfo.

Git checks: `git diff --name-status` lists exactly four allowed tracked files; `git diff --check` exits 0 with only LF-to-CRLF notices. Evidence: `git_diff_name_status_*`, `git_diff_check_*`.

## 4. Remaining risks and limits

- Tripinfo represents only completed vehicles; primary system TTS therefore remains the ID-level online integral, with tripinfo used only for reconciliation.
- Terminal incomplete exposure is counted only through the 360-second horizon and is not extrapolated.
- Origin accounting deliberately depends on frozen generated-ID prefixes `main_` and `ramp_`; other IDs remain explicitly unknown.
- CR-03 failure-stage schema and CR-04 resume behavior remain unimplemented. No safety metrics, thresholds, route generation, or research interpretation changed.
