# Task 3 / CR-05 acceptance report

Status: CR-05 correction round 1/5 is complete. CR-01 through CR-04 were not started. This report concerns run-output isolation and frozen-network integrity only; it does not make traffic-study conclusions.

## 1. Files changed

Production scope:

- `build_network.py`
- `env.py`
- `scan_demand.py`

Test scope:

- `tests/test_build_network.py`
- `tests/test_env_runner.py`
- `tests/test_scan_demand.py`

Acceptance evidence is under `outputs/cr05_acceptance/`. Existing user files under `docs/`, `network/`, and `outputs/audit_0_3/` were not modified or removed. Commit: `none`.

## 2. Implementation and correction-round changes

The base CR-05 implementation freezes the generated network and the five source XML hashes at batch preflight, verifies them before and after each run, builds no shared network during a run, and redirects each run's route, additional XML, detector output, SUMO logs, configuration, windows, and summary into that run's output directory.

Correction round 1 addresses all three reviewer findings:

- Same experiment ID under the same output root now gets an atomic `attempt_0001`, `attempt_0002`, ... directory allocated with `mkdir(exist_ok=False)` retry. `RunResult.experiment_id` is unchanged and `RunResult.output_dir` is the allocated attempt.
- Scan roots are atomically allocated as the timestamp followed by `_0001`, `_0002`, ... on collision, including simultaneous starts within one second.
- A pre-run frozen mismatch propagates before attempt/SUMO work. A post-run mismatch writes `windows.csv` and a machine-readable invalid `summary.csv`, then re-raises `FrozenNetworkMismatchError`; `scan_demand` therefore aborts immediately.
- A run-local detector template is accepted only when it contains exactly the eight required, unique, non-path-like IDs and each element has an output file attribute. Output filenames are constructed only after validation, and every resolved target must be a direct child of the run-local `native/` directory.

The repeated 60-second real SUMO acceptance used the same output root and experiment ID. It produced `attempt_0001` and `attempt_0002`, both valid with two windows and eight native detector XML files. The complete hash manifest of `attempt_0001` was unchanged after `attempt_0002`; generated-file intersection was empty.

## 3. Commands and exact results

All Python commands used Codex bundled Python 3.12 with `SUMO_HOME=D:\sumo-1.25.0` and `D:\sumo-1.25.0\bin` on `PATH`.

Base CR-05 RED:

```text
python -m pytest tests/test_build_network.py tests/test_env_runner.py tests/test_scan_demand.py -q
```

Result: exit 1, `7 failed, 5 passed`. Evidence: `red_focused_*`.

Correction round 1 RED, captured before production fixes:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr05_acceptance\correction1_red_tmp
```

Result: exit 1, `5 failed, 9 passed in 5.69s`. Expected failures covered attempt allocation, injected atomic collision, post-run mismatch propagation, traversal rejection, and same-timestamp scan collision. Evidence: `correction1_red_*`.

Correction round 1 GREEN:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr05_acceptance\correction1_green_tmp
```

Result: exit 0, `14 passed in 5.37s`. Evidence: `correction1_green_*`.

All CR-05 focused tests:

```text
python -m pytest tests/test_build_network.py tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr05_acceptance\correction1_focused_tmp
```

Result: exit 0, `20 passed in 6.25s`. Evidence: `correction1_focused_*`.

Isolated full suite, from `outputs/cr05_acceptance/pytest_sandbox/project/`:

```text
python -m pytest -q --basetemp ..\correction1_full_tmp
```

Result: exit 0, `32 passed in 6.00s`. The real-workspace before/after guard reports `unchanged=true`, `changed_paths=[]`. Evidence: `correction1_full_pytest_*`, `pytest_sandbox_copy_manifest.json`, and `full_pytest_real_workspace_hash_guard.json`.

Same-root real SUMO acceptance:

```text
python outputs\cr05_acceptance\correction1_acceptance_runner.py
```

Result: exit 0; both attempts valid, attempt names exactly `attempt_0001` and `attempt_0002`, native counts `[8, 8]`, first-attempt hashes unchanged, shared generated files `[]`, and real workspace network hashes unchanged. Evidence: `correction1_acceptance_*`, `correction1_same_root_attempt_check.json`, and `correction1_real_workspace_hash_guard.json`.

## 4. Remaining risks and limits

- The frozen-network guard samples integrity before and after a run; it cannot detect a transient mid-run mutation that is fully restored before the post-run check.
- Standalone `run_experiment` still offers a compatibility preflight when no frozen object is supplied. Formal batch execution uses one explicit batch preflight and reuses its frozen object.
- Existing user-owned untracked detector XML and `network/merge.net.xml` remain untouched. Their hashes stayed unchanged throughout the real acceptance and isolated test verification.
- This correction intentionally does not add the broader CR-03 failure-stage schema, change traffic metrics, implement resume semantics, or begin CR-01 through CR-04.
