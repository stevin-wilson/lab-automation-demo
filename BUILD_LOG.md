# Build log

Honest notes on how this repo was built with AI assistance: what was generated, what was changed, what broke. Newest at the bottom.

## Template

    ## YYYY-MM-DD HH:MM - Task N: <name>
    Decision / change:
    Why:
    What the AI generated vs. what I changed:
    What broke and how I found it:
    What I learned (one sentence):

## 2026-10-01 22:30 - Task 1: Models and validation (V1)

Decision / change:
Implemented models.py and validation.py with comprehensive worklist validation according to the spec.

Why:
Core foundation for the lab automation system - all requests flow through this validation layer before any device interaction occurs.

What the AI generated vs. what I changed:
The AI generated the exact code from the brief without changes. All code was used as-is, following the spec verbatim.

What broke and how I found it:
RED: Initial pytest run showed `ModuleNotFoundError: No module named 'labdemo.models'` - expected since the module didn't exist yet. GREEN: After implementing both models.py and validation.py, all 15 tests passed immediately. No breaking issues during pre-commit checks.

What I learned (one sentence):
Pydantic's permissive models paired with a centralized validation function provide clean separation between request parsing and business rule enforcement.

## 2026-10-01 - Task 2: PyLabRobot simulator adapter

Decision / change:
Added `pylabrobot` 0.2.2 and `simulator.py`, a chatterbox-backend adapter with two 96-well plates (SRC1, P1), a tip rack and a trash. Added `tests/conftest.py` with `SpySimulator` and the `spy` and `client` fixtures.

Why:
Gives the API a real PyLabRobot call path to drive while moving nothing. The spy lets later tests prove the device was or was not called, and inject a failure on the Nth call.

What the AI generated vs. what I changed:
Code is verbatim from the brief, with one change: the lazy `labdemo.api` import in `conftest.py` carries `# ty: ignore[unresolved-import]`. `api.py` does not exist until Task 3 and the ty pre-commit hook would otherwise fail the commit. Remove the ignore when Task 3 lands.
The brief's PyLabRobot names were already verified against 0.2.2. The names an LLM would guess from older docs are wrong: `LiquidHandlerChatterboxBackend` (not `ChatterBoxBackend`), `cor_96_wellplate_360uL_Fb` (not `Cor_96_wellplate_360ul_Fb`), and `drop_tips` takes a list.

What broke and how I found it:
RED: `uv run pytest tests/test_simulator.py -q` gave `ModuleNotFoundError: No module named 'labdemo.simulator'`. GREEN: the simulator test passed, and the full suite is 20 passed (18 validation, 1 smoke, 1 simulator). `uv add pylabrobot` installed cleanly on Windows ARM64. No PyLabRobot deprecation warnings (a `-W error` run also passes). The chatterbox backend prints to stdout, including a non-ASCII micro sign in the table header that renders as a replacement character in the Windows console.

What I learned (one sentence):
PyLabRobot's chatterbox backend gives a real call path and visible output with no hardware, so the adapter stays tiny.

## 2026-10-01 - Task 3: Device state machine (F3, part 1)

Decision / change:
Implemented device.py with a full state machine (OFFLINE, IDLE, BUSY, ERROR, UNKNOWN_OUTCOME, NEEDS_HUMAN) and execute() that transitions through BUSY and ERROR on fault, landing in NEEDS_HUMAN. Added 37 tests covering illegal transitions, fault injection, and the clear() recovery path.

Why:
The device owns sequencing and state; execute() combines state transitions with simulator calls to model the real run lifecycle and error handling.

What the AI generated vs. what I changed:
All code is verbatim from the brief. No changes were made; the brief provided the exact test and implementation code to use.

What broke and how I found it:
RED: Initial pytest run showed `ModuleNotFoundError: No module named 'labdemo.device'` - expected. After implementing device.py, GREEN: 37 tests passed (27 generated illegal-transition cases [36 state pairs minus 9 legal ones] + 10 others: connect 1, successful_run 1, armed_fault 1, simulator_exception 1, clear 1, clear_refused 5). All format, lint, and type checks passed. The illegal-transition test is generated from the ALLOWED table, so any missing transitions in the state machine are caught automatically.

What I learned (one sentence):
A Protocol boundary (Simulator) decouples the state machine from the hardware driver, making it trivial to inject a spy or fault.

## 2026-10-01 - Task 4: Command ledger (F1, part 1)

Decision / change:
Implemented ledger.py with SQLite-backed CommandRecord, CommandStatus, EventKind, and Ledger class for idempotent command tracking and event logging. Added 6 comprehensive tests covering command persistence, primary key enforcement, and destination volume derivation.

Why:
The ledger provides the idempotency key (command_id as PRIMARY KEY) that lets the API safely retry failed worklist submissions without duplicate execution, and records all state transitions and faults for auditability and recovery.

What the AI generated vs. what I changed:
All code is verbatim from the brief. No changes were made; the brief provided the exact test and implementation code to use.

What broke and how I found it:
RED: Initial pytest run showed `ModuleNotFoundError: No module named 'labdemo.ledger'` - expected. After implementing ledger.py, GREEN: 6 tests passed. All format, lint, and type checks passed. Tests verify: unknown commands return None, commands are recorded as IN_PROGRESS before execution, command_id is enforced as PRIMARY KEY (IntegrityError on duplicate), records survive database reopening, dest_volumes correctly sums DONE commands plus completed portion of FAILED ones, and events are returned newest-first with optional limit.

What I learned (one sentence):
SQLite's row factory and context managers make it trivial to build a durable, transactional command ledger that integrates naturally with Pydantic models.

## 2026-10-01 - Task 5: Orchestrator API (A1, V1, F1, F3)

Decision / change:
Added api.py (FastAPI app factory `create_app`) and tests/test_api.py (14 tests). POST /commands checks in a fixed order: ledger lookup, validation, device state, then execute. A resend of ANY known command_id (done, failed or in_progress) never re-executes and returns the stored record with duplicate=true; reusing a command_id with a different worklist is a 409. Recovery after a failure is a human clear plus a NEW command_id. Also added python-dotenv and a load_dotenv() call at the top of create_app so LABDEMO_DB can come from the environment or .env, and removed the temporary ty ignore on the create_app import in tests/conftest.py.

Why:
The ledger lookup has to come before validation, otherwise a resend of a command that filled a well would be validated against volumes that already include it and be rejected as an overfill instead of answered as a duplicate. Recording the command before executing means a lost reply can never cause a second run.

What the AI generated vs. what I changed:
Code is verbatim from the brief, plus the two controller additions (python-dotenv with load_dotenv(), and removing the stale ty ignore in conftest.py).

What broke and how I found it:
RED: first pytest run on tests/test_api.py gave 14 errors, all ModuleNotFoundError: No module named 'labdemo.api', raised by the client fixture. GREEN: 14 passed in tests/test_api.py, 77 passed in the whole suite. ruff format, ruff check and ty check were clean. Smoke test: uvicorn started and GET /device returned state IDLE; I then stopped the server and deleted labdemo.db.

What I learned (one sentence):
Where the idempotency check sits in the request pipeline is itself a behavior that needs its own test, because getting the order wrong looks fine until a resend hits a nearly full well.

## 2026-10-01 - Task 6: AI draft gate (AI1)

Decision / change:
Added ai_draft.py (the model drafts a worklist through a forced propose_worklist tool call, the same validate() the API uses checks it, a person approves, then it is submitted), tests/test_ai_gate.py (9 tests) and two recordings. The model's schema (DraftWorklist) has no command_id, so the code assigns it and the model cannot choose it. Invalid or malformed output returns exit code 1 and never reaches the approval prompt or the API. Neither recording is a live model response: no ANTHROPIC_API_KEY was set in the environment and there is no .env file, so no live call was made. recordings/column-1-50ul.json (eight 50 µL transfers A1->A1 to H1->H1) and recordings/overdose-250ul.json (one 250 µL transfer) are both hand-written and labeled "origin": "hand-written". The live path (draft_live, --record) is implemented but has not been run.

Why:
An LLM is useful for turning a plain-English request into a worklist, but it must not be trusted. Putting the same validator and a human approval step between the model and the API keeps the safety rules in code, not in the prompt.

What the AI generated vs. what I changed:
Code is verbatim from the brief except for the typing of the Anthropic tool definition. ty reported the ignore on dict(block.input) as unused, so I removed it. The ignore on tools=[DRAFT_TOOL] was needed (ty reported invalid-argument-type without it), so instead I typed DRAFT_TOOL as anthropic.types.ToolParam (imported under TYPE_CHECKING) and removed that ignore too; ty is clean with no ignore comments in the file. python-dotenv was already a dependency from Task 5, so pyproject.toml and uv.lock are untouched.

What broke and how I found it:
RED: first pytest run on tests/test_ai_gate.py gave ModuleNotFoundError: No module named 'labdemo.ai_draft'. GREEN: 6 passed in tests/test_ai_gate.py, 83 passed in the whole suite. Review fix: main() now catches anthropic.APIError and RuntimeError from draft_live, prints a message pointing to --replay and returns 4 (spec §6); 3 tests added (RED: 2 failed, 7 passed; GREEN: 9 passed in tests/test_ai_gate.py, 86 passed in the whole suite). ruff format, ruff check and ty check were clean. Offline replay of overdose-250ul exited 1 and printed "Validation FAILED, approval not offered" with the 250 exceeds-max error. In this Git Bash capture the µ in "exceeds max 200 µL" printed as a replacement character (console encoding); the tests check the string in memory and pass.

What I learned (one sentence):
Leaving command_id out of the model's schema is a structural guarantee, which is stronger than telling the model in a prompt not to choose one.

## 2026-10-01 - Task 7: Dashboard, demo script and run documentation

Decision / change:
Added dashboard.py (read-only Streamlit page: state badge, since-time, armed fault, event table with rejected/refused/failed/duplicate rows highlighted, Refresh button, error banner when the API is unreachable), demo.py (scripted A1, V1, F1, F3 run), tests/test_dashboard.py (1 test) and a full README rewrite (quickstart, scenarios, layout, sustainability, known limitations, troubleshooting). The README limitations section states the honest gaps: device state is not persisted across a restart, a crash can leave a command in_progress, a failed SQLite write or a cancelled request can leave the device BUSY/ERROR, UNKNOWN_OUTCOME is modeled but not exercised, and both recordings are hand-written because the live --record path has never been run against the real Anthropic API.

Why:
The spec asks for a status view (section 5.6) and for a README that takes a new user from clone to a running demo. The demo script makes the four control-side behaviors visible in about one second, and the README states what is not done.

What the AI generated vs. what I changed:
Code is from the brief with two changes. (1) The dashboard test calls .run(timeout=30) instead of .run(): the first full-suite run failed with "AppTest script run timed out after 3(s)", because AppTest's default 3 second limit was exceeded on a cold import of streamlit and pandas; the same test passed when rerun alone and in later full runs. (2) In demo.py the brief's "# type: ignore[union-attr]" was flagged by ty (unresolved-attribute), so I used "# ty: ignore[unresolved-attribute]" as the brief allows; a hasattr guard did not work because ty then reported call-non-callable. width="stretch" was accepted by the installed Streamlit, so use_container_width was not needed.

What broke and how I found it:
RED: tests/test_dashboard.py failed with FileNotFoundError because dashboard.py did not exist. GREEN: 87 passed in the whole suite (86 before this task plus 1), with ruff format, ruff check and ty check clean. Real run: uvicorn and streamlit (headless, port 8501) in the background, dashboard health check returned ok, and demo.py exited 0 with every step at its expected HTTP code (200, 422, 200 duplicate, 500, 200, 409, 200). I have no browser here, so I did not look at the dashboard and the highlighting is NOT visually verified; as a substitute, Streamlit's AppTest against the live API rendered without an exception and showed "Device: IDLE" and 16 event rows. Screenshots are still to be taken by hand. Both servers were stopped, a port check found nothing listening on 8000 or 8501, and labdemo.db was deleted. I also ran the two documented ai_draft replays: overdose-250ul exited 1 with the validation error, column-1-50ul showed the table and, with "n" piped in, exited 2 with "Not approved. Nothing was sent."

What I learned (one sentence):
A test framework's default timeout is part of the test, and a 3 second limit on a cold import is a flaky test waiting to happen.

## 2026-10-01 - Task 7 review fixes: dotenv in dashboard and demo, README accuracy

Decision / change:
dashboard.py and demo.py now call load_dotenv() before reading LABDEMO_API_URL, so the README and spec section 7 claim (environment or .env) is true for them as it already was for the API and ai_draft. Two tests in tests/test_dashboard.py copy the script next to a temporary .env (load_dotenv searches from the script's own directory, not the working directory) and check the unreachable-API banner text and demo.API_URL. The README's recordings limitation no longer claims any SDK or mocked-transport check; it says only that both recordings are hand-written and that draft_live has no automated test. README troubleshooting now shows how to set LABDEMO_API_URL in PowerShell and bash.

What broke and how I found it:
A reviewer found that the README said .env was read by the dashboard and demo when it was not, and that my README claimed a mocked-transport check that nothing in the repo supports. RED: the two new tests failed (banner and API_URL showed http://127.0.0.1:8000 instead of http://127.0.0.1:9). GREEN: 3 passed in tests/test_dashboard.py, 89 passed in the whole suite; ruff format, ruff check and ty check clean.

What I learned (one sentence):
A documentation claim about configuration is a behavior, and it needs a test just like code does.

## 2026-10-01 - Task 6b: first live run of the AI gate

Decision / change:
The user added a real API key to the gitignored .env and the live path was run for the first time. It was called against the real API at least three times (the first call, a probe request for 250 uL, and the `--record` run). The first call failed with HTTP 400: tool_choice of type "tool" or "any" is not supported by claude-sonnet-5-5. Changed draft_live to tool_choice {"type": "auto"}, added the sentence "Always answer by calling the propose_worklist tool." to SYSTEM_PROMPT, and raised max_tokens from 1024 to 4096 (adaptive thinking is on by default for this model and uses output tokens). Added decision D9, corrected spec section 5.5 and D6, and corrected the README. recordings/column-1-50ul.json is now a genuine live recording, recorded 2026-10-01 local time (the run's UTC stamp was 2026-10-02) (origin live, 8 transfers of 50 uL, A1->A1 to H1->H1), made with `--record` through the real code path; replaying it offline prints origin live. recordings/overdose-250ul.json stays hand-written.

Why:
The Task 6 graceful-failure path (exit 4 pointing to --replay) worked as designed, but the forced tool call my code (and the brief) used does not exist on this model. A mock-only test suite could never have found that.

What the AI generated vs. what I changed:
The forced tool_choice came from the brief. I replaced it with auto plus an instruction, and the controller had already verified that request against the real API. Two regression tests (fake anthropic.Anthropic, no network) pin the request shape: tool_choice is auto, the tool is propose_worklist, max_tokens is at least 4096, the system prompt names the tool, and a response with no tool_use block raises RuntimeError.

What broke and how I found it:
RED: test_live_request_does_not_force_a_tool failed on the tools/tool_choice assertion (1 failed, 10 passed); the no-tool-block test already passed because that guard existed. GREEN: 11 passed in tests/test_ai_gate.py, 91 passed in the whole suite. Finding: asked live for "Move 250 uL from SRC1 A1 to P1 A1", the model split it into two valid 125 uL transfers by itself, so a live overdose could not be captured and the overdose fixture stays hand-written. The validator is defense in depth for when a model does not self-correct. No automated test calls the real API.

What I learned (one sentence):
Tests with a fake client prove your code handles the response shape you imagined, so one real call early is what finds that the request itself is no longer allowed.
