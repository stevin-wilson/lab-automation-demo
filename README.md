# lab-automation-demo

A small **simulated** lab-automation integration: an orchestration API that validates worklists, enforces explicit device states and idempotent commands, and drives a **simulated** liquid handler. It also has a gated path where an LLM drafts a worklist and a human approves it.

> This is a learning project. It is not a real instrument integration. The liquid handler is a simulator, all data is **synthetic**, and replayed LLM output is labeled **recorded response**.

**Status:** the implementation is complete and tested. It was built test-first, on a feature branch, from the spec below.

## How this repo was built (spec-driven)

1. **Spec**: [docs/specs/2026-09-30-simulated-lab-integration.md](docs/specs/2026-09-30-simulated-lab-integration.md) defines the goals, the acceptance scenarios (A1, V1, F1, F3, AI1) and the architecture.
2. **Decisions**: [docs/decisions.md](docs/decisions.md) records what was chosen, why, and when to revisit it.
3. **Plan**: `docs/plans/` holds the test-first implementation plan.
4. **Failing test, then code**: each scenario has a test marked `@pytest.mark.spec("<ID>")`. A traceability test fails CI if a scenario in the spec has no test.
5. **CI**: every push runs pre-commit (ruff, ty) and pytest from a clean environment.

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python (3.12) for you.
- Optional: an `ANTHROPIC_API_KEY`, only to re-record the AI step.

## Quickstart

Install the dependencies once:

```
uv sync
```

Then use three terminals, all from the repo root.

Terminal 1, the API (an empty `labdemo.db` is created on first run; interactive docs at <http://127.0.0.1:8000/docs>):

```
uv run uvicorn labdemo.api:create_app --factory
```

Terminal 2, the dashboard (opens at <http://localhost:8501>):

```
uv run streamlit run dashboard.py
```

Terminal 3, the scripted demo:

```
uv run python demo.py
```

`demo.py` prints one `HTTP <code> (expected <code>)` line per step and ends with `Done.` when every step behaved as expected. Press **Refresh** on the dashboard to see the device state and the event log.

To reset, stop the API and delete `labdemo.db`. Destination wells fill up across runs, so after about six runs the A1 step is rejected as an overfill until you reset.

Both the dashboard and the demo read `LABDEMO_API_URL` (default `http://127.0.0.1:8000`). Configuration can also come from a `.env` file; copy `.env.example` to start.

## Scenarios

| ID | How to trigger it | What you see on the dashboard |
|---|---|---|
| A1: valid worklist runs | `demo.py` step "A1: valid worklist runs" (HTTP 200, `status: done`, 8 transfers) | State goes `IDLE` -> `BUSY` -> `IDLE`. `submitted`, `transition` and `done` rows. |
| V1: invalid worklist rejected | `demo.py` step "V1: invalid well and 250 uL rejected" (HTTP 422, one message per problem) | A highlighted `rejected` row. No `submitted` row and no `BUSY`: the device was never contacted. |
| F1: resend is a duplicate | `demo.py` step "F1: resend of the A1 command is a duplicate" (HTTP 200, `duplicate: true`) | A highlighted `duplicate` row. No second run. |
| F3: fault, then human clear | `demo.py` steps starting "F3": it arms a fault with `POST /device/fault`, the run fails after one transfer (HTTP 500), the next command gets 409, then `POST /device/clear` returns the device to `IDLE` | `failed` and `refused` rows highlighted. The state badge shows `NEEDS_HUMAN` until the clear, then `IDLE`. |
| AI1: AI draft gate | `uv run python -m labdemo.ai_draft --replay overdose-250ul`: the 250 uL transfer is rejected and approval is not offered. `uv run python -m labdemo.ai_draft --replay column-1-50ul`: valid, asks `Approve and submit? [y/N]`, and needs the API running if you answer `y` | `overdose-250ul` sends nothing, so the dashboard does not change. `column-1-50ul` after `y` appears as a normal command in the log. |

## AI step

- Replay is offline: `--replay NAME` loads `recordings/NAME.json` and never touches the network. Output is labeled "recorded response" plus the recording's origin.
- The model's output is never trusted. The same `validate()` the API uses checks it, a person approves it, and the code (not the model) assigns the `command_id`.
- Re-recording needs `ANTHROPIC_API_KEY`. Copy `.env.example` to `.env`, set the key, then run `uv run python -m labdemo.ai_draft --record NAME "your request"`.
- Each recording carries an `origin` field, `live` or `hand-written`. Both recordings in `recordings/` are currently `hand-written` (see the limitations below).

## Development

```
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check
uv run ty check
```

## Project layout

| Path | Responsibility |
|---|---|
| `src/labdemo/models.py` | Pydantic models (`Transfer`, `Worklist`) and 96-well plate constants. |
| `src/labdemo/validation.py` | A pure function that returns a list of human-readable errors for a worklist. |
| `src/labdemo/device.py` | Owns the device state machine and calls a `Simulator` once per transfer. |
| `src/labdemo/simulator.py` | The PyLabRobot-backed `Simulator`: pick up tip, aspirate, dispense, drop tip, on the chatterbox backend. |
| `src/labdemo/ledger.py` | Persists command records (the idempotency key) and an event log in SQLite, and derives how much liquid each destination well already holds. |
| `src/labdemo/api.py` | HTTP endpoints that run validation, the idempotency check, the state check and execution, in that order. |
| `src/labdemo/ai_draft.py` | Turns a plain-English request into a worklist with an LLM, validates it and asks a human to approve it. |
| `dashboard.py` | A read-only Streamlit status page showing device state and the event log. |
| `demo.py` | A scripted run through A1, V1, F1, F3 against a running API. |
| `recordings/` | Recorded LLM responses used for offline replay and as test fixtures. |
| `tests/` | The pytest suite. No test touches the network. |
| `docs/` | The spec, the implementation plan and the decision records. |

## Sustainability: built in a weekend, still maintainable

- **Quality gates from the first commit:** pre-commit (ruff, ty) and CI on every push and PR.
- **Tests that document behavior:** each acceptance scenario is a named test with an ID, and a traceability test fails CI if the spec and the tests drift apart.
- **Reproducible environment:** `uv.lock` is committed and the Python version is pinned, so a fresh clone is one command.
- **Clear boundaries:** validation is a pure function, the state machine is a small table, and the simulator sits behind the `Simulator` protocol, so a vendor SDK replaces one class.
- **Recorded decisions:** `docs/decisions.md` says what was chosen, why, and when to revisit it; `BUILD_LOG.md` records what the AI got wrong.
- **Stated limitations and next steps:** the next section.

## Known limitations and what I'd do next

In production priority order (spec section 11):

1. Exercise `UNKNOWN_OUTCOME` (reply timeout leads to `NEEDS_HUMAN`, no auto-retry). The state is modeled but nothing triggers it.
2. Async execution (`202` plus status polling) and a separate edge-agent process per instrument.
3. Containerize with one Dockerfile, then Compose and Kubernetes.
4. A mock plate reader, file ingestion and per-well lineage (well to command to run).
5. Auth, operator identity on `clear`, and a scheduler (for example Cellario or Green Button Go) between the orchestrator and devices.

Also true today:

- **Retries and command IDs.** A resend of any known `command_id` returns the stored record and never re-executes, whether the command is `done`, `failed` or `in_progress`. Recovery after a failure is a human clear plus a **new** `command_id`.
- **Restart behavior.** Device state is not persisted across an API restart; the API starts in `IDLE` again. An `in_progress` command left behind by a crash is never retried and needs a human to look at it.
- **Persistence failures.** If writing a transition event to SQLite fails after the device state has already changed, the device can be left `BUSY` or `ERROR`, or a command left `in_progress`, until the API restarts. A request cancelled mid-transfer also leaves the device `BUSY`. `UNKNOWN_OUTCOME` is the state meant for that case, but it is modeled and not exercised.
- **Recordings are hand-written.** Both files in `recordings/` have `origin: hand-written`. The live `--record` path has never been run against the real Anthropic API; it was checked against the SDK and a mocked transport only. Re-record with a real key before relying on it.
- **Dashboard.** It is covered by one automated test (the unreachable-API banner). Its rendering against a live API is a manual check.

## Troubleshooting

- **Port 8000 is in use.** Start the API on another port, `uv run uvicorn labdemo.api:create_app --factory --port 8001`, and point the other programs at it by setting `LABDEMO_API_URL=http://127.0.0.1:8001` (in the environment or `.env`).
- **The dashboard shows "Cannot reach the API at ...".** The API is not running or `LABDEMO_API_URL` points at the wrong place. Start the API (terminal 1) and press Refresh.
- **`demo.py` says the device is not `IDLE`.** A previous run left it in `NEEDS_HUMAN`. Restart the API, or `POST /device/clear`.
- **Missing API key.** Live mode prints that `ANTHROPIC_API_KEY` is not set and exits with code 4. Use `--replay NAME` for the offline demo, or set the key in `.env`.
- **PyLabRobot prints each operation to the console.** That is the simulator (the chatterbox backend), not an error.
- **A garbled micro sign (`µ`) in a Windows console.** That is the console encoding. The data is correct.
