# Task 3 / CR-03 acceptance report

Status: CR-03 correction round 3 is complete after the final data-integrity review. CR-05, CR-01, and CR-02 behavior remains accepted; CR-04 was not started. No research scan was run.

## 1. Modified files

Tracked changes are exactly:

- `env.py`
- `scan_demand.py`
- `tests/test_env_runner.py`
- `tests/test_scan_demand.py`

Evidence is under `outputs/cr03_acceptance/`. No changes were made to metrics, classification, route/network construction, docs, network XML, or prior CR evidence. Commit: `none`.

## 2. Implementation

- `run_experiment` now assigns stable stages: `prepare_output`, `network_preflight`, `prepare_inputs`, `traci_import`, `sumo_start`, `run_loop`, `post_run_verify`, and `output_write`.
- Once an experiment ID and attempt directory exist, ordinary exceptions produce an invalid existing-schema `summary.csv` where writable and a machine-readable `attempt.json`.
- Every completed attempt, including success, writes `cr03-attempt-v1` with experiment/status/valid, failure stage/type/message, retryability, parsed attempt number, timezone-aware timestamps, and absolute output directory.
- Controller-name and configuration validation still raise before attempt creation. A pre-run frozen mismatch retains the CR-05 no-attempt hard gate; a post-run mismatch writes invalid evidence and a non-retryable attempt record before propagating.
- Cleanup calls only `conn.close()` and only after a connection was acquired. Global `traci.close()` is never called; existing logs are not removed or overwritten by cleanup.
- `scan_demand` reads attempt metadata into the expanded `run_index.csv`. An escaped ordinary per-run failure adds an index row and scan-level ledger row, then continues with later plans.
- If an attempt directory/record cannot be created, `failure_ledger.csv` preserves plan identity, stage/type/message, retryability, and timezone timestamp.
- Shared batch preflight failures write the ledger then rethrow. Frozen mismatches still abort immediately. `resume` behavior remains unchanged.
- Every escaped failure carries a structured `failure_context` with the original stage/type/message/retryability and, when available, experiment, output directory, and attempt. Failure while writing `attempt.json` raises a wrapper that retains this original context instead of replacing it with the record-write error.
- Per-run frozen-network mismatch now writes the planned-run ledger before immediate propagation. A post-run mismatch also writes an invalid index row from its existing attempt; a pre-run mismatch has no attempt/index row by design.
- Escaped ordinary failures append invalid `EpisodeSummary` placeholders, so all-failed demand pairs still produce `insufficient_valid_runs` / `no_valid_pairs` classification rows.
- `run_index.status` remains compatibility-safe `valid|invalid`; `attempt_status` separately records `success|failed`.
- Missing or malformed `attempt.json` is recorded as non-retryable `output_parse`, with an invalid index row and ledger entry. The scan replaces any returned valid traffic summary with an existing-schema invalid placeholder carrying planned identity and the parse failure reason, then continues.
- Normal returned runs now pass `result.experiment_id` into a strict `cr03-attempt-v1` reader. It rejects missing fields, wrong schema/identity, invalid status or types, attempt-directory/output-path mismatches, naive or reversed timestamps, and inconsistent success/failed records with explicit `ValueError`.
- Frozen/escaped-failure metadata lookup remains best-effort: strict self-consistency failures return no optional attempt metadata and never replace the original failure.

Explicit retry rules do not inspect exception messages:

- retryable: `prepare_output`, `sumo_start`, `run_loop`, `output_write`, and unexpected escaped `run_experiment` failures;
- non-retryable: `network_preflight`, `prepare_inputs`, `traci_import`, `post_run_verify`, `output_parse`, unknown stages, and every `FrozenNetworkMismatchError`.

## 3. Commands and exact results

All tests used bundled Python 3.12 with SUMO 1.25.0 configured in `SUMO_HOME`/`PATH`.

Initial RED:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\red_tmp
```

Result: exit 1, `9 failed, 16 passed in 6.98s`. Failures covered absent success/failure attempt records, uncaught preflight/import failures, wrong connection cleanup, missing frozen mismatch machine record, scan abort on ordinary failure, absent failure ledger, and absent shared-preflight ledger. Evidence: `red_focused_*`.

CR-03 GREEN:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\green_tmp
```

Result: exit 0, `25 passed in 7.11s`. Tests inject `network_preflight`, `traci_import`, `sumo_start`, and `run_loop` failures and assert invalid summary, exact attempt schema/stage/type/retryability, timezone timestamps, success records, frozen propagation, and connection-local cleanup. Evidence: `green_focused_*`.

Correction round 1 RED (kept separate from the original RED):

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction1_red_tmp
```

Result: exit 1, `8 failed, 21 passed in 8.24s`. The failures reproduced all review findings: missing original failure context after permanent attempt-write failure, absent frozen-mismatch ledger/index, incompatible index status values, exception-type stage guessing, missing all-failed classification, and malformed attempt JSON aborting the scan. Evidence: `correction1_red_*`.

Correction round 1 GREEN:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction1_green_tmp
```

Result: exit 0, `29 passed in 8.04s`. Evidence: `correction1_green_*`.

Correction round 2 RED:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction2_red_tmp
```

Result: exit 1, `2 failed, 28 passed in 8.41s`. Tests constructed malformed and missing `attempt.json` with returned `summary.valid=True`; they exposed retryable parse failures and would have admitted machine-invalid runs into a valid seed pair. Evidence: `correction2_red_*`.

Correction round 2 GREEN:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction2_green_tmp
```

Result: exit 0, `30 passed in 8.43s`. The malformed one-sided case and all-missing case both retain a classification row with `valid_seed_count=0`, `insufficient_valid_runs`, and `no_valid_pairs`; ledger/index retryability is false. Evidence: `correction2_green_*`.

Correction round 3 RED:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction3_red_tmp
```

Result: exit 1, `15 failed, 30 passed in 9.40s`. Parameterized cases showed that `{}`, wrong schema/experiment/path/attempt, invalid enums and booleans, invalid timestamp semantics, and inconsistent status payloads were all silently accepted. Evidence: `correction3_red_*`.

Correction round 3 GREEN:

```text
python -m pytest tests/test_env_runner.py tests/test_scan_demand.py -q --basetemp outputs\cr03_acceptance\correction3_green_tmp
```

Result: exit 0, `45 passed in 9.28s`. Every corrupt case becomes hard non-retryable `output_parse`, the later planned run continues, and the invalid side cannot create a valid classification pair. Evidence: `correction3_green_*`.

Regression:

- Correction CR-02/CR-01 focused (`tests/test_metrics.py tests/test_env_runner.py`): `28 passed in 7.78s`; evidence `correction1_cr02_cr01_regression_*`.
- Correction CR-05 focused (`tests/test_build_network.py tests/test_env_runner.py tests/test_scan_demand.py`): `35 passed in 8.23s`; evidence `correction1_cr05_regression_*`.
- Correction isolated complete suite: `54 passed in 9.07s`; evidence `correction1_full_pytest_isolated_*` and refreshed `pytest_sandbox_copy_manifest.json`.
- Correction-2 CR-02/CR-01 focused: `28 passed in 7.69s`; evidence `correction2_cr02_cr01_regression_*`.
- Correction-2 CR-05 focused: `36 passed in 8.70s`; evidence `correction2_cr05_regression_*`.
- Correction-2 isolated complete suite: `55 passed in 9.25s`; evidence `correction2_full_pytest_isolated_*` and refreshed `pytest_sandbox_copy_manifest.json`.
- Correction-3 CR-02/CR-01 focused: `28 passed in 7.91s`; evidence `correction3_cr02_cr01_regression_*`.
- Correction-3 CR-05 focused: `51 passed in 9.92s`; evidence `correction3_cr05_regression_*`.
- Correction-3 isolated complete suite: `70 passed in 9.81s`; evidence `correction3_full_pytest_isolated_*` and refreshed `pytest_sandbox_copy_manifest.json`.
- Isolated test guard: `unchanged=true`, `changed_paths=[]`, covering allowed tracked files and all real `network/*.xml`; evidence `full_pytest_real_workspace_hash_guard.json`.
- `git diff --name-status` lists exactly the four allowed tracked files. `git diff --check` exits 0 with only LF-to-CRLF working-tree notices; latest evidence `correction3_git_diff_name_status_*` and `correction3_git_diff_check_*`.

## 4. Remaining risks and limits

- A failure that removes write access to the attempt directory can prevent attempt-local summary/JSON; the propagated structured context and scan-level ledger are the designed fallback.
- A returned run whose attempt record is missing/corrupt is conservatively indexed invalid even if its returned summary is valid; classification receives an invalid placeholder, preventing machine-invalid runs from forming valid pairs.
- `traci_import` and deterministic preparation failures are deliberately non-retryable in this stage; changing retry policy requires an explicit protocol revision, not message matching.
- A process crash or forced termination can occur before `finished_at`/attempt evidence is written; durable crash recovery belongs to later operational work.
- CR-04 resume/config matching remains unimplemented. No scan thresholds, scenario, perturbation, metrics, or research conclusions changed.
- Strict CR-03 validation deliberately does not compare configuration or content hashes; those checks remain CR-04 scope.
