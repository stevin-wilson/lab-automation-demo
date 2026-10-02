# Plan: Minimal mock plate reader + inbox watcher + per-well lineage

## Context

Spec §11 item 4 ("a mock plate reader, file ingestion and per-well lineage") was cut by D7 for the weekend build. Stevin has chosen to build a minimal version before the 2026-10-05 interview. Right now the repo only sends commands to a device; this adds the return path, where results come back from an instrument and are linked to the commands that made them. That return path is closest to Stevin's data-side background. The same control-side patterns stay in place: validate before recording, an idempotency key, never silently retry, and label synthetic data.

**Budget:** about 2.5 to 3 hours. The talk still comes first on Sunday. The work goes on a branch `feature/plate-reader-watcher`, which is merged by PR only when green.

## Design (smallest version that keeps the architecture honest)

```
plate_reader.py --writes CSV (tmp + rename)--> inbox/ --polled by--> watcher.py
watcher.py --POST /readouts--> api.py --validate_readout--> ledger (readouts table + lineage snapshot)
watcher moves file to inbox/processed/ or inbox/rejected/ (+ .error.txt)
```

- **The watcher is an API client, like `ai_draft`; it is not in-process.** The API stays the only SQLite writer, so D3 still holds, and the API does all validation and recording. New decision **D11**.
- **Poll instead of using `watchdog`.** It adds no dependency, needs about 10 lines and is easy to explain. Default every 2 s, with `--once` for tests and smoke.
- **Idempotency key = SHA-256 of the file bytes (`readout_id`).** The same file a second time returns a duplicate (`200`, `duplicate: true`) and is not stored twice. The same id with a different body returns `409`, mirroring D8.
- **Delivery is at least once.** If the API can't be reached, the file stays in `inbox/` and is retried on the next poll. That is safe because the receiver is idempotent.
- **Half-written files:** the mock reader writes `*.csv.tmp` and then calls `os.replace`, and the watcher only picks up `*.csv`. This is a real pattern worth explaining.
- **Lineage is snapshotted at ingest.** For each well it stores `{well, value, volume_ul, command_ids}`, built from the completed transfers in the ledger. A well with `command_ids: []` is a reading that no command can explain, which is a useful data-quality signal.
- **Synthetic labelling:** the CSV's first line is `# SYNTHETIC DATA - mock plate reader`. Values are seeded `random.Random(seed).uniform(0.04, 1.2)`.
- **Out of scope (D7 still stands for these):** heatmap, lineage UI and dashboard changes. Readout events appear in the existing event log anyway.

## Changes

### Code
- `src/labdemo/models.py`: add `Reading(well: str, value: float)` and `Readout(readout_id, plate, source_file, readings: list[Reading])`.
- `src/labdemo/validation.py`: add pure `validate_readout(readout) -> list[str]`, which checks for a non-empty id, a plate in `DEST_PLATES`, 1–96 readings, valid wells (reuse `WELL_IDS`), no duplicate wells and finite values. It returns all errors in the same message style as `validate()`.
- `src/labdemo/ledger.py`:
  - Factor out `_completed_transfers()` (done = all transfers, failed = first `transfers_completed`) and use it in both `dest_volumes()` and a new `well_commands() -> dict[(plate, well), list[command_id]]`.
  - New `readouts` table (`readout_id PK, plate, source_file, readout_json, lineage_json, ingested_at`), plus `get_readout()` and `add_readout()`.
  - `EventKind.READOUT = "readout"`.
- `src/labdemo/api.py`:
  - `POST /readouts` runs in the same order as `/commands`: ledger lookup (duplicate or `409`), then validate (`422 {errors}` plus a `rejected` event), then build lineage from `well_commands()` and `dest_volumes()`, then store, then return `200 {readout_id, duplicate, lineage}`.
  - `GET /readouts/{readout_id}` returns `200` with the stored readout and lineage, or `404`.
  - It needs no device or state check: reading results doesn't touch the liquid handler.
- `src/labdemo/plate_reader.py` (new, about 40 lines): `write_readout(inbox, plate="P1", seed=None) -> Path` writes all 96 wells as `plate,well,od600` using tmp then rename. The CLI is `python -m labdemo.plate_reader --inbox inbox [--plate P1] [--seed N]`.
- `src/labdemo/watcher.py` (new, about 80 lines):
  - `parse_csv(path) -> Readout` skips `#` lines and computes the SHA-256 id. Unparseable files go to `rejected/` locally.
  - `process_inbox(inbox, http: httpx.Client) -> list[Outcome]`: `200` moves the file to `processed/`, `422`/`409` move it to `rejected/` with `<name>.error.txt`, and a connection error leaves the file in place.
  - The CLI is `python -m labdemo.watcher --inbox inbox [--once] [--interval 2]`. Exit codes with `--once`: 0 if all were ingested or duplicates, 1 if any were rejected, 2 if the API was unreachable. For each file it prints `readout <id[:12]>: 96 wells, 8 traced to commands`.
  - It takes an `httpx.Client` so tests can pass FastAPI's `TestClient` (a subclass).
- `.gitignore`: add `inbox/`.

### Tests (marked with new spec IDs; update `tests/test_traceability.py` expected set to add R1, R2, R3)
- `tests/test_watcher.py` (uses the existing `client` fixture and `tmp_path`):
  - **R1**: after an A1-style command fills P1 column 1, `write_readout` then `process_inbox` gives a file in `processed/`, a `GET /readouts/{id}` whose lineage traces A1–H1 to that `command_id` with 50 µL, an empty `command_ids` for A2, and a `readout` event.
  - **R2**: a CSV with an `I1` well and a `nan` value gives a `422` listing both errors, a file in `rejected/` plus `.error.txt`, no stored readout, a `rejected` event, and `spy.calls` unchanged.
  - **R3**: the same file dropped again gives `duplicate: true`, still exactly one stored readout and a `duplicate` event. The same id with a different body gives `409`.
  - API unreachable (an `httpx.Client` with a transport that raises `ConnectError`) leaves the file in `inbox/`.
  - Only `*.csv` is picked up, so `*.csv.tmp` is ignored.
- `tests/test_validation.py`: unit cases for `validate_readout`.
- `tests/test_ledger.py`: `well_commands()` counts only the completed part of a failed command, mirroring the existing `dest_volumes` test.

### Smoke / CI
- `scripts/smoke.py`: after the `demo.py` step, add a step that runs `plate_reader --inbox <tmp>/inbox --seed 1` (exit 0), then `watcher --inbox <tmp>/inbox --once` (exit 0). This needs `STEPS` to accept the tmp dir, so build the list inside `main()`. CI's `e2e` job picks it up with no workflow change.

### Docs (same PR; the spec stays the source of truth)
- `docs/plans/2026-10-02-plate-reader-watcher-plan.md`: this plan, committed first.
- Spec:
  - Remove the plate-reader item from §2 non-goals and keep "heatmap / lineage UI".
  - Add R1–R3 rows to §3.
  - Add `plate_reader.py` and `watcher.py` rows to the §4 table and mermaid diagram.
  - New §5.7 "Readout ingestion", plus the `/readouts` rows in §5.4 and the `readout` event kind.
  - Add tests to the §8 table.
  - Shrink §11 item 4 to "heatmap and lineage UI; vendor file formats".
  - Bump the status line.
- `docs/decisions.md`: add D11 (the watcher is an API client that polls, with a content-hash idempotency key and at-least-once delivery). Add a one-line note on D7 that the plate reader and file watcher were later added by D11.
- `README.md`: add a "Plate reader and watcher" run section with copy-paste commands (start watcher, drop a readout, `curl /readouts/<id>`), and update the roadmap list.
- `BUILD_LOG.md`: add an entry covering what was generated, what changed and what broke.

## Verification
1. `uv run pytest` passes, including the traceability test with R1–R3.
2. `uv run pre-commit run --all-files` passes (ruff, ruff-format, ty).
3. `uv run python scripts/smoke.py` prints PASS for every step, including the new reader/watcher step.
4. Manual run: start the API, run `demo.py`, run `watcher --inbox inbox` in one terminal and `plate_reader --inbox inbox` in another. Check that the watcher prints "8 traced to commands", that `curl /readouts/<id>` shows A1–H1 linked to the `demo-…-a1` command, and that the dashboard event log shows a `readout` row.
5. Push the branch, then open a PR whose CI (`check` + `e2e`) is green before merging.
