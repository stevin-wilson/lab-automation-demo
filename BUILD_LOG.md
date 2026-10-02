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
