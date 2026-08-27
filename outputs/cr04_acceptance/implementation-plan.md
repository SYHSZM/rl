# CR-04 Resume and Identity Implementation Plan

> **Execution:** Inline TDD in the current session. No commit is permitted; each task ends with captured RED/GREEN evidence.

**Goal:** Add deterministic scan identity, immutable manifests, strict artifact validation, and true resume without starting any experiment scan or research work.

**Architecture:** `scan_demand.py` remains the only production file changed. Pure helpers build canonical identities and validate disk artifacts; `scan_demand()` orchestrates atomic directory allocation, frozen-network reuse, decisions, new attempts, deterministic index rebuild, and classification selection.

**Tech Stack:** Python standard library (`hashlib`, `json`, `csv`, `tempfile`, `os`, `pathlib`), existing dataclasses and CR-01/02/03/05 APIs.

## Global Constraints

- Allowed production/test files: `scan_demand.py`, `tests/test_scan_demand.py`.
- Evidence only under `outputs/cr04_acceptance/`; do not modify prior evidence.
- Do not modify `env.py`, metrics, classify, network construction, docs, or `network/`.
- No CR-04 implementation may launch the 770-run matrix, scenario research, calibration, perturbations, or automatic parallelism.
- Core code hash files are exactly: `build_network.py`, `classify.py`, `controllers.py`, `env.py`, `experiment_config.py`, `metrics.py`, `rou_generate.py`, `scan_demand.py`.
- Protocol source is exactly `docs/experiment_protocol_v1.0.md`, version `1.0`.
- Scene source files are exactly `build_network.NETWORK_SOURCE_FILENAMES`.
- Manifest JSON writes use a same-directory temporary file and `os.replace`.
- Final handoff is `commit none`.

---

### Task 1: Core RED fixtures and resume behavior

**Files:**
- Modify: `tests/test_scan_demand.py`

**Interfaces:**
- Consumes: existing `scan_demand(config, output_root, controllers, resume, use_gui)`.
- Produces expected API: `scan_demand(..., scan_id: str | None = None) -> Path` and CLI `--scan-id`.

- [ ] Add a complete attempt fixture that writes the real CR-03 attempt, config/network snapshots, summary/windows/system exposure, route/additional/tripinfo/logs, eight native XML files, and a literal CR-04 artifact manifest.
- [ ] Create one scan containing four planned cases: fully valid, invalid, missing/hash-corrupt, and foreign identity.
- [ ] Assert resume skips only the valid case, calls `run_experiment` for the other three, retains failed and successful attempts in deterministic `run_index.csv`, and a second resume makes no attempt and leaves index bytes unchanged.
- [ ] Run focused tests and capture `outputs/cr04_acceptance/red_focused_*`; expected failure is missing `scan_id`/resume behavior.

### Task 2: Deterministic scan identity and atomic scan manifest

**Files:**
- Modify: `scan_demand.py`
- Test: `tests/test_scan_demand.py`

**Interfaces:**
- Produces `_scan_identity(config, controllers) -> dict`, `_identity_sha256(identity) -> str`, `_prepare_scan_directory(...) -> tuple[Path, dict, FrozenNetwork]`, and `_atomic_write_json(path, payload)`.

- [ ] Test automatic config changes yield different scan IDs, explicit ID mismatch raises without modifying the old manifest, existing resume does not call preflight, and `resume=False` allocates `_0001`.
- [ ] Test scan manifest fields include canonical config, controllers, scene hashes, named core file hashes plus combined hash, protocol version/hash, and schema versions.
- [ ] Implement canonical JSON SHA-256 and safe explicit ID validation.
- [ ] For a new directory call preflight once and atomically write `cr04-scan-manifest-v1`; for existing resume strictly validate manifest, rebuild `FrozenNetwork`, and call `verify_frozen_network` without preflight.
- [ ] Run the new identity tests to GREEN.

### Task 3: Attempt artifact manifest and strict validator

**Files:**
- Modify: `scan_demand.py`
- Test: `tests/test_scan_demand.py`

**Interfaces:**
- Produces `_write_artifact_manifest(attempt_dir, planned_identity)`, `_validate_attempt_artifacts(...) -> tuple[EpisodeSummary | None, str]`, and `_planned_identity(...)`.

- [ ] Test artifact manifest schema/identity/file entries, atomic replacement, and exclusion of the manifest from its own file list.
- [ ] Implement file SHA-256/byte inventory over all attempt files except the artifact manifest.
- [ ] Strictly require CR-03 success+valid attempt; one matching valid summary; exact config and frozen-network snapshot; matching window and system-exposure schemas; route/additional/tripinfo/two logs; eight native XML files; and matching size/hash for every listed file.
- [ ] Make invalid, missing, corrupt, foreign, or schema-wrong attempts return a deterministic reason and never qualify for skip/classification.
- [ ] Run artifact tests to GREEN.

### Task 4: Resume orchestration, index rebuild, decisions, classification

**Files:**
- Modify: `scan_demand.py`
- Test: `tests/test_scan_demand.py`

**Interfaces:**
- Produces `_rebuild_run_index(scan_dir, planned_map, scan_manifest) -> list[dict]`, `_write_resume_decisions(...)`, and latest-valid selection per planned run.

- [ ] Before each run, select the newest fully valid matching attempt; with `resume=True` record `skip`, otherwise record `retry` or `run` and call `run_experiment` once.
- [ ] After every returned or escaped attempt, atomically write its artifact manifest and rebuild index from disk in stable demand/controller/seed/attempt order.
- [ ] Preserve frozen mismatch immediate propagation and ordinary escaped failure continuation.
- [ ] At completion rebuild index from disk, atomically write decisions, and classify only newest fully valid matching summaries; use the current invalid result/placeholder if none exists.
- [ ] Run all `tests/test_scan_demand.py` to GREEN.

### Task 5: Acceptance and guards

**Files:**
- Create: `outputs/cr04_acceptance/task-report.md`
- Create: `outputs/cr04_acceptance/evidence_manifest.json`

- [ ] Capture focused CR-04 GREEN.
- [ ] Run CR-03/02/01/05 focused regressions and a real short SUMO regression only; do not run the research matrix.
- [ ] Copy the project to an isolated sandbox and run the complete suite.
- [ ] Hash real `network/*.xml` before/after and require no changes.
- [ ] Capture `git diff --name-status` and `git diff --check`; require tracked changes only in allowed files.
- [ ] Write exact RED/GREEN/regression results, limits, and `commit none`; generate a SHA-256 evidence manifest.
