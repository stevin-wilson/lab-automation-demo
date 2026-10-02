# Simulated Lab Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small, fully simulated lab integration: an API that validates worklists, runs them on a simulated liquid handler through an explicit state machine with idempotent commands, plus a gated AI-drafting step and a status dashboard.

**Architecture:** One FastAPI process holds the orchestrator endpoints, a hand-written device state machine and a SQLite ledger. The device calls a `Simulator` protocol, implemented by PyLabRobot's chatterbox backend. A Streamlit page and two scripts (`demo.py`, `ai_draft.py`) are clients of the HTTP API.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic, PyLabRobot (simulated backend), SQLite (`sqlite3`), Streamlit, Anthropic SDK, python-dotenv, pytest, ruff, ty, pre-commit.

**Spec:** [docs/specs/2026-09-30-simulated-lab-integration.md](../specs/2026-09-30-simulated-lab-integration.md). Read it first. Scenario IDs (A1, V1, F1, F3, AI1) come from its §3. Decisions are in [docs/decisions.md](../decisions.md).

## Global Constraints

Every task's requirements include these.

- Python 3.12 (pinned in `.python-version`); dependencies, venv and lock file via **uv** only (`uv add`, `uv run`, commit `uv.lock`). No pip, Poetry or venv.
- **ruff** (lint + format, line length 100), **ty** (types), **pre-commit**, **pytest**. No black, flake8, isort or mypy.
- Plates: source `SRC1`, destination `P1`, both 96-well; wells `A1`–`H12`, matched exactly (no case or whitespace normalization).
- Volume per transfer: at least 1 µL, at most 200 µL, and finite. A destination well holds at most 300 µL. A worklist has 1–96 transfers.
- HTTP codes: `200` (done or duplicate), `422 {errors}` (invalid), `409` (device not `IDLE`, or `command_id` reused with a different worklist), `500` (command failed).
- The command is recorded as `in_progress` **before** the device runs. A resend never runs again. A `409` for "device not IDLE" is not recorded.
- The LLM never chooses `command_id`. LLM output is validated by the same `validate()` and shown to a human before anything is submitted.
- Model id for the AI step: `claude-sonnet-5-5`.
- Runs offline. Only `ai_draft` live mode needs `ANTHROPIC_API_KEY`. Env vars: `LABDEMO_DB` (default `./labdemo.db`), `LABDEMO_API_URL` (default `http://127.0.0.1:8000`).
- Label simulator output and data as **synthetic**; replayed LLM output as **recorded response**. No employer-proprietary content anywhere.
- Every test that proves a scenario carries `@pytest.mark.spec("<ID>")`.

## Conventions for every task

- **Red, then green.** Write the tests, run them and watch them fail for the expected reason, then implement. Record the failure you saw in the commit message body. (We commit tests and code together, because the `ty` hook cannot pass on tests that import modules that don't exist yet, and we never skip hooks.)
- **Commit** with the scenario ID in the subject, e.g. `V1: validate worklists before any device call`. Pre-commit runs ruff and ty. If ruff reformats files the first commit attempt fails: run `git add -A` and commit again.
- **Build log.** After each task, append an entry to `BUILD_LOG.md` using its template. Write what actually happened (what the AI generated, what you changed, what broke, one thing learned). Never invent entries; if nothing broke, say so.
- Run commands from the repo root. Work on branch `feature/init`.

## Review Focus

Inputs the spec implies but its scenarios don't name. Each has a test, in the task shown.

1. **A resend of a command that filled a well** must return `duplicate`, not an overfill `422`. (Task 5)
2. **NaN or infinite volumes** must be rejected, not slip past `<` and `>` comparisons. (Task 1)
3. **The same `command_id` with a different worklist** must be a `409`, not the old result. (Task 5)
4. **Lowercase or padded wells** (`a1`, `A1 `) must be rejected, not normalized. (Task 1)
5. **`clear` while `BUSY`.** `BUSY → IDLE` is a legal transition, so `clear` must check for `NEEDS_HUMAN` explicitly. (Task 3)

---

## Task 0: Branch setup

- [ ] **Step 1: Make sure the spec and plan are on `main`, then branch**

```bash
git switch main
git pull --ff-only origin main
git status --short
git switch -c feature/init
```

Expected: `status` prints nothing. Run `git log --oneline -3` and confirm the spec and plan commits are there.

---

## Task 1: Models and validation (V1)

**Files:**
- Create: `src/labdemo/models.py`, `src/labdemo/validation.py`, `tests/test_validation.py`, `BUILD_LOG.md`

**Interfaces:**
- Produces:
  - `models.py`: `Transfer(source_well: str, dest_well: str, volume_ul: float)`, `Worklist(command_id: str, source_plate: str, dest_plate: str, transfers: list[Transfer])`, constants `WELL_IDS`, `MIN_VOLUME_UL`, `MAX_VOLUME_UL`, `MAX_WELL_VOLUME_UL`, `MAX_TRANSFERS`, `SOURCE_PLATES`, `DEST_PLATES`.
  - `validation.py`: `validate(worklist: Worklist, dest_volumes: Mapping[tuple[str, str], float] | None = None) -> list[str]`. Keys are `(plate, well)`. Returns every error; `[]` means valid.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_validation.py`:

```python
import pytest

from labdemo.models import Transfer, Worklist
from labdemo.validation import validate


def make(
    *transfers: Transfer,
    source: str = "SRC1",
    dest: str = "P1",
    command_id: str = "cmd-1",
) -> Worklist:
    return Worklist(
        command_id=command_id,
        source_plate=source,
        dest_plate=dest,
        transfers=list(transfers),
    )


def xfer(src: str = "A1", dst: str = "A1", vol: float = 50.0) -> Transfer:
    return Transfer(source_well=src, dest_well=dst, volume_ul=vol)


@pytest.mark.spec("V1")
def test_valid_worklist_has_no_errors():
    assert validate(make(xfer(), xfer("H12", "H12", 200.0), xfer(vol=1.0))) == []


@pytest.mark.spec("V1")
@pytest.mark.parametrize(
    ("transfer", "expected"),
    [
        (xfer(src="I1"), "transfers[0].source_well 'I1' is not a valid well (A1-H12)"),
        (xfer(dst="A13"), "transfers[0].dest_well 'A13' is not a valid well (A1-H12)"),
        (xfer(src="a1"), "transfers[0].source_well 'a1' is not a valid well (A1-H12)"),
        (xfer(dst="A1 "), "transfers[0].dest_well 'A1 ' is not a valid well (A1-H12)"),
        (xfer(vol=250.0), "transfers[0].volume_ul=250 exceeds max 200 µL"),
        (xfer(vol=0.5), "transfers[0].volume_ul=0.5 is below min 1 µL"),
        (xfer(vol=0.0), "transfers[0].volume_ul=0 is below min 1 µL"),
        (xfer(vol=-5.0), "transfers[0].volume_ul=-5 is below min 1 µL"),
        (xfer(vol=float("nan")), "transfers[0].volume_ul must be a finite number"),
        (xfer(vol=float("inf")), "transfers[0].volume_ul must be a finite number"),
    ],
)
def test_bad_transfer_is_reported(transfer, expected):
    assert expected in validate(make(transfer))


@pytest.mark.spec("V1")
def test_unknown_and_wrong_role_plates_are_reported():
    errors = validate(make(xfer(), source="P1", dest="SRC1"))
    assert "source_plate 'P1' is not a known source plate (known: SRC1)" in errors
    assert "dest_plate 'SRC1' is not a known destination plate (known: P1)" in errors


@pytest.mark.spec("V1")
def test_transfer_count_limits():
    assert "transfers must contain 1-96 items (got 0)" in validate(make())
    too_many = [xfer("A1", "A1", 1.0)] * 97
    assert "transfers must contain 1-96 items (got 97)" in validate(make(*too_many))


@pytest.mark.spec("V1")
def test_destination_overfill_within_one_worklist():
    errors = validate(make(xfer(dst="B2", vol=200.0), xfer("A1", "B2", 101.0)))
    assert errors == ["transfers[1].dest_well P1/B2 would hold 301 µL, exceeding max 300 µL"]


@pytest.mark.spec("V1")
def test_destination_overfill_counts_volume_already_in_the_well():
    errors = validate(make(xfer(dst="B2", vol=200.0)), {("P1", "B2"): 150.0})
    assert errors == ["transfers[0].dest_well P1/B2 would hold 350 µL, exceeding max 300 µL"]


@pytest.mark.spec("V1")
def test_exactly_full_well_is_allowed():
    assert validate(make(xfer(dst="B2", vol=200.0), xfer("A1", "B2", 100.0))) == []


@pytest.mark.spec("V1")
def test_all_errors_are_reported_together():
    errors = validate(make(xfer(src="I1", dst="A13", vol=250.0), source="NOPE"))
    assert len(errors) == 4


@pytest.mark.spec("V1")
def test_blank_command_id_is_reported():
    assert "command_id must not be empty" in validate(make(xfer(), command_id="  "))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_validation.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'labdemo.models'`.

- [ ] **Step 3: Implement `models.py`**

Create `src/labdemo/models.py`:

```python
"""Request models and plate constants shared by the API, the validator and the simulator."""

from pydantic import BaseModel

ROWS = "ABCDEFGH"
COLUMNS = range(1, 13)
WELL_IDS: frozenset[str] = frozenset(f"{row}{col}" for row in ROWS for col in COLUMNS)

MIN_VOLUME_UL = 1.0
MAX_VOLUME_UL = 200.0  # tip maximum
MAX_WELL_VOLUME_UL = 300.0
MAX_TRANSFERS = 96

SOURCE_PLATES: frozenset[str] = frozenset({"SRC1"})
DEST_PLATES: frozenset[str] = frozenset({"P1"})


class Transfer(BaseModel):
    """Move volume_ul microlitres from one source well to one destination well."""

    source_well: str
    dest_well: str
    volume_ul: float


class Worklist(BaseModel):
    """A client-identified batch of transfers. command_id is the idempotency key."""

    command_id: str
    source_plate: str
    dest_plate: str
    transfers: list[Transfer]
```

The models are deliberately permissive: every rule lives in `validate()`, so clients get one error format.

- [ ] **Step 4: Implement `validation.py`**

Create `src/labdemo/validation.py`:

```python
"""Pure validation of a worklist against plate and volume limits. No I/O, no device access."""

import math
from collections.abc import Mapping

from labdemo.models import (
    DEST_PLATES,
    MAX_TRANSFERS,
    MAX_VOLUME_UL,
    MAX_WELL_VOLUME_UL,
    MIN_VOLUME_UL,
    SOURCE_PLATES,
    WELL_IDS,
    Worklist,
)

DestVolumes = Mapping[tuple[str, str], float]


def _plate_error(field: str, name: str, role: str, known: frozenset[str]) -> str:
    return f"{field} {name!r} is not a known {role} plate (known: {', '.join(sorted(known))})"


def validate(worklist: Worklist, dest_volumes: DestVolumes | None = None) -> list[str]:
    """Return every problem found as a readable message. An empty list means valid.

    dest_volumes maps (plate, well) to the volume already in that destination well.
    """
    existing = dest_volumes or {}
    errors: list[str] = []

    if not worklist.command_id.strip():
        errors.append("command_id must not be empty")
    if worklist.source_plate not in SOURCE_PLATES:
        errors.append(_plate_error("source_plate", worklist.source_plate, "source", SOURCE_PLATES))
    if worklist.dest_plate not in DEST_PLATES:
        errors.append(_plate_error("dest_plate", worklist.dest_plate, "destination", DEST_PLATES))

    count = len(worklist.transfers)
    if not 1 <= count <= MAX_TRANSFERS:
        errors.append(f"transfers must contain 1-{MAX_TRANSFERS} items (got {count})")

    added: dict[str, float] = {}
    for i, transfer in enumerate(worklist.transfers):
        prefix = f"transfers[{i}]"
        for field, well in (
            ("source_well", transfer.source_well),
            ("dest_well", transfer.dest_well),
        ):
            if well not in WELL_IDS:
                errors.append(f"{prefix}.{field} {well!r} is not a valid well (A1-H12)")

        volume = transfer.volume_ul
        if not math.isfinite(volume):
            errors.append(f"{prefix}.volume_ul must be a finite number")
        elif volume < MIN_VOLUME_UL:
            errors.append(f"{prefix}.volume_ul={volume:g} is below min {MIN_VOLUME_UL:g} µL")
        elif volume > MAX_VOLUME_UL:
            errors.append(f"{prefix}.volume_ul={volume:g} exceeds max {MAX_VOLUME_UL:g} µL")
        elif transfer.dest_well in WELL_IDS:
            well = transfer.dest_well
            added[well] = added.get(well, 0.0) + volume
            total = existing.get((worklist.dest_plate, well), 0.0) + added[well]
            if total > MAX_WELL_VOLUME_UL:
                errors.append(
                    f"{prefix}.dest_well {worklist.dest_plate}/{well} would hold "
                    f"{total:g} µL, exceeding max {MAX_WELL_VOLUME_UL:g} µL"
                )
    return errors
```

- [ ] **Step 5: Create the build log**

Create `BUILD_LOG.md`:

```markdown
# Build log

Honest notes on how this repo was built with AI assistance: what was generated, what was changed, what broke. Newest at the bottom.

## Template

    ## YYYY-MM-DD HH:MM - Task N: <name>
    Decision / change:
    Why:
    What the AI generated vs. what I changed:
    What broke and how I found it:
    What I learned (one sentence):
```

Then add the first real entry for this task at the bottom, following the template.

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_validation.py -q`
Expected: all pass.

Run: `uv run ruff format && uv run ruff check && uv run ty check`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "V1: validate worklists before any device call

Red run: ModuleNotFoundError for labdemo.models, then all validation tests pass.
Covers wells, volumes (incl. NaN/inf), plates, counts, overfill, blank command_id."
```

---

## Task 2: PyLabRobot simulator adapter

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `src/labdemo/simulator.py`, `tests/conftest.py`, `tests/test_simulator.py`

**Interfaces:**
- Consumes: `Transfer` from `models.py`.
- Produces:
  - `PyLabRobotSimulator` with `async setup() -> None`, `async stop() -> None`, `async transfer(source_plate: str, dest_plate: str, transfer: Transfer) -> None`. Plate names are `SRC1` and `P1`.
  - `tests/conftest.py`: class `SpySimulator` (wraps a real `PyLabRobotSimulator`; `.calls: list[tuple[str, str, Transfer]]`; `.fail_on_call: int | None` raises `RuntimeError("simulated hardware error")` on that 1-based call) and fixtures `spy` and `client`.

PyLabRobot is pre-1.0 and renames things between releases. The names below were checked against pylabrobot 0.2.2 on this machine: `LiquidHandlerChatterboxBackend` (not `ChatterBoxBackend`), `cor_96_wellplate_360uL_Fb` (not `Cor_96_wellplate_360ul_Fb`), `opentrons_96_filtertiprack_200ul`, and `drop_tips` takes a list. If `uv add pylabrobot` fails to install on this machine, stop and report. The fallback is a 20-line fake `Simulator` with the same three methods.

- [ ] **Step 1: Add the dependency**

Run: `uv add pylabrobot`
Expected: resolves and installs without error.

- [ ] **Step 2: Write the failing test**

Create `tests/test_simulator.py`:

```python
import asyncio

from labdemo.models import Transfer
from labdemo.simulator import PyLabRobotSimulator


def test_simulator_runs_pick_up_aspirate_dispense_drop(capsys):
    async def scenario() -> None:
        sim = PyLabRobotSimulator()
        await sim.setup()
        await sim.transfer("SRC1", "P1", Transfer(source_well="A1", dest_well="B2", volume_ul=50.0))
        await sim.transfer(
            "SRC1", "P1", Transfer(source_well="H12", dest_well="H12", volume_ul=200)
        )
        await sim.stop()

    asyncio.run(scenario())

    output = capsys.readouterr().out.lower()
    assert "aspirat" in output
    assert "dispens" in output
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_simulator.py -q`
Expected: `ModuleNotFoundError: No module named 'labdemo.simulator'`.

- [ ] **Step 4: Implement the adapter**

Create `src/labdemo/simulator.py`:

```python
"""PyLabRobot-backed simulator: two 96-well plates, a tip rack and a trash on a chatterbox backend.

The chatterbox backend moves nothing and prints each operation. Well-volume tracking is off, so
the plates hold no liquid; the ledger is what tracks volumes. Tips are not consumed.
"""

from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend
from pylabrobot.resources import (
    Coordinate,
    Deck,
    Trash,
    cor_96_wellplate_360uL_Fb,
    opentrons_96_filtertiprack_200ul,
)

from labdemo.models import Transfer


class PyLabRobotSimulator:
    def __init__(self) -> None:
        deck = Deck(size_x=500, size_y=400, size_z=200)
        self._plates = {
            "SRC1": cor_96_wellplate_360uL_Fb(name="SRC1"),
            "P1": cor_96_wellplate_360uL_Fb(name="P1"),
        }
        self._tips = opentrons_96_filtertiprack_200ul(name="TIPS1")
        self._trash = Trash(name="trash", size_x=100, size_y=100, size_z=50)
        deck.assign_child_resource(self._plates["SRC1"], location=Coordinate(10, 10, 0))
        deck.assign_child_resource(self._plates["P1"], location=Coordinate(150, 10, 0))
        deck.assign_child_resource(self._tips, location=Coordinate(300, 10, 0))
        deck.assign_child_resource(self._trash, location=Coordinate(10, 200, 0))
        backend = LiquidHandlerChatterboxBackend(num_channels=1)
        self._handler = LiquidHandler(backend=backend, deck=deck)

    async def setup(self) -> None:
        await self._handler.setup()

    async def stop(self) -> None:
        await self._handler.stop()

    async def transfer(self, source_plate: str, dest_plate: str, transfer: Transfer) -> None:
        source = self._plates[source_plate].get_item(transfer.source_well)
        dest = self._plates[dest_plate].get_item(transfer.dest_well)
        await self._handler.pick_up_tips(self._tips["A1"])
        await self._handler.aspirate([source], vols=[transfer.volume_ul])
        await self._handler.dispense([dest], vols=[transfer.volume_ul])
        await self._handler.drop_tips([self._trash])
```

- [ ] **Step 5: Create the shared test fixtures**

Create `tests/conftest.py`:

```python
"""Shared fixtures: a spy around the real PyLabRobot simulator, and an API test client."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from labdemo.models import Transfer
from labdemo.simulator import PyLabRobotSimulator


class SpySimulator:
    """Wraps the real simulator and records every transfer call.

    Tests use .calls to prove the device was (or was not) called. Setting .fail_on_call to N makes
    the Nth call raise before reaching the simulator.
    """

    def __init__(self) -> None:
        self.inner = PyLabRobotSimulator()
        self.calls: list[tuple[str, str, Transfer]] = []
        self.fail_on_call: int | None = None

    async def setup(self) -> None:
        await self.inner.setup()

    async def stop(self) -> None:
        await self.inner.stop()

    async def transfer(self, source_plate: str, dest_plate: str, transfer: Transfer) -> None:
        self.calls.append((source_plate, dest_plate, transfer))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated hardware error")
        await self.inner.transfer(source_plate, dest_plate, transfer)


@pytest.fixture
def spy() -> SpySimulator:
    return SpySimulator()


@pytest.fixture
def client(tmp_path, spy: SpySimulator) -> Iterator[TestClient]:
    from labdemo.api import create_app  # imported here so earlier tasks' tests don't need it

    with TestClient(create_app(str(tmp_path / "test.db"), spy)) as test_client:
        yield test_client
```

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_simulator.py -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: pass and clean. If ty rejects PyLabRobot types, fix with the narrowest correct annotation or `# ty: ignore[<rule>]` with a one-line reason, and note it in the build log.

- [ ] **Step 7: Build log, then commit**

Append a `BUILD_LOG.md` entry (note which PyLabRobot names had changed from what an LLM would guess).

```bash
git add -A
git commit -m "Add PyLabRobot simulator adapter and test spy

Red run: ModuleNotFoundError for labdemo.simulator. Names verified against
pylabrobot 0.2.2 (LiquidHandlerChatterboxBackend, cor_96_wellplate_360uL_Fb)."
```

---

## Task 3: Device state machine (F3, part 1)

**Files:**
- Create: `src/labdemo/device.py`, `tests/test_device.py`

**Interfaces:**
- Consumes: `Transfer`, `Worklist` from `models.py`; `SpySimulator` and the `spy` fixture from `tests/conftest.py`.
- Produces in `device.py`:
  - `DeviceState` (`StrEnum`: `OFFLINE`, `IDLE`, `BUSY`, `ERROR`, `UNKNOWN_OUTCOME`, `NEEDS_HUMAN`; values equal names).
  - `ALLOWED: dict[DeviceState, frozenset[DeviceState]]`.
  - `IllegalTransition(Exception)`, `ExecutionFailed(Exception)` with `.transfers_completed: int`, `SimulatedFault(Exception)`.
  - `Simulator` (`Protocol`: `setup`, `stop`, `transfer`).
  - `Device(simulator: Simulator, on_transition: Callable[[DeviceState, DeviceState, str], None] | None = None)` with attributes `state`, `since: str` (ISO timestamp), `armed_fault: bool`, and methods `transition(to, reason)`, `async connect()`, `async shutdown()`, `arm_fault()`, `clear(reason: str)`, `async execute(worklist) -> int` (transfers completed; raises `ExecutionFailed` on failure).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_device.py`:

```python
import asyncio

import pytest

from labdemo.device import ALLOWED, Device, DeviceState, ExecutionFailed, IllegalTransition
from labdemo.models import Transfer, Worklist

S = DeviceState


def make_worklist(n: int) -> Worklist:
    return Worklist(
        command_id=f"cmd-{n}",
        source_plate="SRC1",
        dest_plate="P1",
        transfers=[
            Transfer(source_well=f"A{i + 1}", dest_well=f"A{i + 1}", volume_ul=10.0)
            for i in range(n)
        ],
    )


def make_device(spy):
    transitions: list[tuple[S, S, str]] = []
    device = Device(spy, on_transition=lambda old, new, why: transitions.append((old, new, why)))
    return device, transitions


def moves(transitions):
    return [(old, new) for old, new, _ in transitions]


@pytest.mark.spec("F3")
@pytest.mark.parametrize(
    ("old", "new"),
    [(o, n) for o in S for n in S if n not in ALLOWED[o]],
)
def test_illegal_transitions_raise(spy, old, new):
    device, transitions = make_device(spy)
    device.state = old
    with pytest.raises(IllegalTransition):
        device.transition(new, "test")
    assert device.state is old
    assert transitions == []


def test_connect_moves_offline_to_idle(spy):
    device, transitions = make_device(spy)
    asyncio.run(device.connect())
    assert device.state is S.IDLE
    assert transitions == [(S.OFFLINE, S.IDLE, "connect + self-test")]


@pytest.mark.spec("A1")
def test_successful_run_returns_to_idle(spy):
    device, transitions = make_device(spy)

    async def scenario() -> int:
        await device.connect()
        return await device.execute(make_worklist(2))

    assert asyncio.run(scenario()) == 2
    assert len(spy.calls) == 2
    assert device.state is S.IDLE
    assert moves(transitions) == [(S.OFFLINE, S.IDLE), (S.IDLE, S.BUSY), (S.BUSY, S.IDLE)]


@pytest.mark.spec("F3")
def test_armed_fault_fires_after_the_first_transfer(spy):
    device, transitions = make_device(spy)
    device.arm_fault()

    async def scenario() -> int:
        await device.connect()
        return await device.execute(make_worklist(3))

    with pytest.raises(ExecutionFailed) as failure:
        asyncio.run(scenario())

    assert failure.value.transfers_completed == 1
    assert len(spy.calls) == 1
    assert device.state is S.NEEDS_HUMAN
    assert device.armed_fault is False
    assert moves(transitions)[-2:] == [(S.BUSY, S.ERROR), (S.ERROR, S.NEEDS_HUMAN)]


@pytest.mark.spec("F3")
def test_simulator_exception_takes_the_same_failure_path(spy):
    spy.fail_on_call = 2
    device, _ = make_device(spy)

    async def scenario() -> int:
        await device.connect()
        return await device.execute(make_worklist(3))

    with pytest.raises(ExecutionFailed) as failure:
        asyncio.run(scenario())

    assert failure.value.transfers_completed == 1
    assert "simulated hardware error" in str(failure.value)
    assert device.state is S.NEEDS_HUMAN


@pytest.mark.spec("F3")
def test_clear_returns_needs_human_to_idle(spy):
    device, transitions = make_device(spy)
    device.arm_fault()

    async def scenario() -> None:
        await device.connect()
        with pytest.raises(ExecutionFailed):
            await device.execute(make_worklist(1))

    asyncio.run(scenario())
    device.clear("operator checked the deck")
    assert device.state is S.IDLE
    assert transitions[-1] == (S.NEEDS_HUMAN, S.IDLE, "operator checked the deck")


@pytest.mark.spec("F3")
@pytest.mark.parametrize("state", [S.OFFLINE, S.IDLE, S.BUSY, S.ERROR, S.UNKNOWN_OUTCOME])
def test_clear_is_refused_unless_needs_human(spy, state):
    # BUSY -> IDLE is a legal transition, so clear() must check the state itself.
    device, transitions = make_device(spy)
    device.state = state
    with pytest.raises(IllegalTransition):
        device.clear("not allowed")
    assert device.state is state
    assert transitions == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_device.py -q`
Expected: `ModuleNotFoundError: No module named 'labdemo.device'`.

- [ ] **Step 3: Implement `device.py`**

Create `src/labdemo/device.py`:

```python
"""Device state machine. Owns what the device is doing and calls a Simulator to do it."""

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from labdemo.models import Transfer, Worklist


class DeviceState(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    BUSY = "BUSY"
    ERROR = "ERROR"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"  # modeled, not exercised (spec section 11)
    NEEDS_HUMAN = "NEEDS_HUMAN"


S = DeviceState

# The whole state machine. Anything not listed here is illegal.
ALLOWED: dict[DeviceState, frozenset[DeviceState]] = {
    S.OFFLINE: frozenset({S.IDLE}),
    S.IDLE: frozenset({S.BUSY, S.OFFLINE}),
    S.BUSY: frozenset({S.IDLE, S.ERROR, S.UNKNOWN_OUTCOME}),
    S.ERROR: frozenset({S.NEEDS_HUMAN}),
    S.UNKNOWN_OUTCOME: frozenset({S.NEEDS_HUMAN}),
    S.NEEDS_HUMAN: frozenset({S.IDLE}),
}


class IllegalTransition(Exception):
    """A state change that is not in ALLOWED, or an operation not valid in this state."""


class SimulatedFault(Exception):
    """The injected fault from arm_fault()."""


class ExecutionFailed(Exception):
    """A run failed part-way. transfers_completed says how far it got."""

    def __init__(self, message: str, transfers_completed: int) -> None:
        super().__init__(message)
        self.transfers_completed = transfers_completed


class Simulator(Protocol):
    """The boundary to the instrument. A vendor SDK or SiLA 2 client would implement this."""

    async def setup(self) -> None: ...

    async def stop(self) -> None: ...

    async def transfer(self, source_plate: str, dest_plate: str, transfer: Transfer) -> None: ...


TransitionListener = Callable[[DeviceState, DeviceState, str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Device:
    def __init__(
        self, simulator: Simulator, on_transition: TransitionListener | None = None
    ) -> None:
        self._simulator = simulator
        self._on_transition = on_transition
        self.state = DeviceState.OFFLINE
        self.since = _now()
        self.armed_fault = False

    def transition(self, to: DeviceState, reason: str) -> None:
        if to not in ALLOWED[self.state]:
            raise IllegalTransition(f"{self.state} -> {to} is not allowed")
        old = self.state
        self.state = to
        self.since = _now()
        if self._on_transition is not None:
            self._on_transition(old, to, reason)

    async def connect(self) -> None:
        await self._simulator.setup()
        self.transition(S.IDLE, "connect + self-test")

    async def shutdown(self) -> None:
        if self.state is S.IDLE:
            self.transition(S.OFFLINE, "shutdown")
        await self._simulator.stop()

    def arm_fault(self) -> None:
        """Make the next run fail after its first transfer completes."""
        self.armed_fault = True

    def clear(self, reason: str) -> None:
        """A human has checked the device. Only valid from NEEDS_HUMAN."""
        if self.state is not S.NEEDS_HUMAN:
            raise IllegalTransition(f"device is {self.state}, not {S.NEEDS_HUMAN}")
        self.transition(S.IDLE, reason)

    async def execute(self, worklist: Worklist) -> int:
        """Run every transfer. Returns how many completed; raises ExecutionFailed on a fault.

        There are no awaits between the caller's IDLE check and the BUSY transition below,
        so two concurrent requests cannot both start a run.
        """
        self.transition(S.BUSY, f"command {worklist.command_id} accepted")
        completed = 0
        try:
            for transfer in worklist.transfers:
                await self._simulator.transfer(worklist.source_plate, worklist.dest_plate, transfer)
                completed += 1
                if self.armed_fault:
                    self.armed_fault = False
                    raise SimulatedFault("injected fault")
        except Exception as exc:
            self.transition(S.ERROR, f"{type(exc).__name__}: {exc}")
            self.transition(S.NEEDS_HUMAN, "automatic: needs human inspection, no retry")
            raise ExecutionFailed(str(exc), completed) from exc
        self.transition(S.IDLE, f"command {worklist.command_id} complete")
        return completed
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_device.py -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: pass and clean.

- [ ] **Step 5: Build log, then commit**

```bash
git add -A
git commit -m "F3: device state machine with fault path to NEEDS_HUMAN

Red run: ModuleNotFoundError for labdemo.device. Illegal-transition tests are generated
from the ALLOWED table. clear() checks NEEDS_HUMAN explicitly because BUSY -> IDLE is legal."
```

---

## Task 4: Command ledger (F1, part 1)

**Files:**
- Create: `src/labdemo/ledger.py`, `tests/test_ledger.py`

**Interfaces:**
- Consumes: `Worklist` from `models.py`.
- Produces in `ledger.py`:
  - `CommandStatus` (`StrEnum`: `IN_PROGRESS="in_progress"`, `DONE="done"`, `FAILED="failed"`).
  - `EventKind` (`StrEnum`: `SUBMITTED`, `REJECTED`, `DUPLICATE`, `REFUSED`, `DONE`, `FAILED`, `TRANSITION`, `FAULT_ARMED`, `CLEARED`; values are the lowercase names, e.g. `"fault_armed"`).
  - `CommandRecord` (pydantic: `command_id`, `status: CommandStatus`, `worklist: Worklist`, `result: dict[str, Any] | None`, `created_at: str`).
  - `Ledger(path: str)` with `get_command(command_id) -> CommandRecord | None`, `start_command(worklist) -> None` (inserts `in_progress`; raises `sqlite3.IntegrityError` on a repeated id), `finish_command(command_id, status, result: dict) -> None`, `dest_volumes() -> dict[tuple[str, str], float]`, `add_event(kind, command_id=None, detail="") -> None`, `events(limit=100) -> list[dict]` (newest first; keys `id`, `at`, `kind`, `command_id`, `detail`), `close() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ledger.py`:

```python
import sqlite3

import pytest

from labdemo.ledger import CommandStatus, EventKind, Ledger
from labdemo.models import Transfer, Worklist


def worklist(command_id: str = "cmd-1", *transfers: Transfer) -> Worklist:
    default = [Transfer(source_well="A1", dest_well="A1", volume_ul=50.0)]
    return Worklist(
        command_id=command_id,
        source_plate="SRC1",
        dest_plate="P1",
        transfers=list(transfers) or default,
    )


@pytest.fixture
def ledger(tmp_path):
    db = Ledger(str(tmp_path / "ledger.db"))
    yield db
    db.close()


def test_unknown_command_is_none(ledger):
    assert ledger.get_command("nope") is None


@pytest.mark.spec("F1")
def test_command_is_recorded_as_in_progress_before_it_runs(ledger):
    ledger.start_command(worklist("cmd-42"))
    record = ledger.get_command("cmd-42")
    assert record is not None
    assert record.status is CommandStatus.IN_PROGRESS
    assert record.worklist == worklist("cmd-42")
    assert record.result is None


@pytest.mark.spec("F1")
def test_command_id_is_the_primary_key(ledger):
    ledger.start_command(worklist("cmd-42"))
    with pytest.raises(sqlite3.IntegrityError):
        ledger.start_command(worklist("cmd-42"))


@pytest.mark.spec("F1")
def test_records_survive_reopening_the_database(tmp_path):
    path = str(tmp_path / "ledger.db")
    first = Ledger(path)
    first.start_command(worklist("cmd-42"))
    first.finish_command("cmd-42", CommandStatus.DONE, {"transfers_completed": 1})
    first.close()

    second = Ledger(path)
    record = second.get_command("cmd-42")
    second.close()
    assert record is not None
    assert record.status is CommandStatus.DONE
    assert record.result == {"transfers_completed": 1}


def test_dest_volumes_count_done_and_completed_part_of_failed(ledger):
    done = worklist(
        "done",
        Transfer(source_well="A1", dest_well="A1", volume_ul=100.0),
        Transfer(source_well="B1", dest_well="A1", volume_ul=50.0),
    )
    failed = worklist(
        "failed",
        Transfer(source_well="A1", dest_well="B1", volume_ul=20.0),
        Transfer(source_well="B1", dest_well="B1", volume_ul=30.0),
    )
    running = worklist("running", Transfer(source_well="A1", dest_well="C1", volume_ul=99.0))
    for item in (done, failed, running):
        ledger.start_command(item)
    ledger.finish_command("done", CommandStatus.DONE, {"transfers_completed": 2})
    ledger.finish_command("failed", CommandStatus.FAILED, {"transfers_completed": 1})

    assert ledger.dest_volumes() == {("P1", "A1"): 150.0, ("P1", "B1"): 20.0}


def test_events_are_newest_first_and_limited(ledger):
    ledger.add_event(EventKind.SUBMITTED, "cmd-1", "first")
    ledger.add_event(EventKind.DONE, "cmd-1", "second")
    ledger.add_event(EventKind.CLEARED, None, "third")

    events = ledger.events(limit=2)
    assert [e["detail"] for e in events] == ["third", "second"]
    assert [e["kind"] for e in events] == ["cleared", "done"]
    assert events[0]["command_id"] is None
    assert set(events[0]) == {"id", "at", "kind", "command_id", "detail"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: `ModuleNotFoundError: No module named 'labdemo.ledger'`.

- [ ] **Step 3: Implement `ledger.py`**

Create `src/labdemo/ledger.py`:

```python
"""SQLite command ledger (the idempotency key) and event log."""

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from labdemo.models import Worklist


class CommandStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class EventKind(StrEnum):
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    DONE = "done"
    FAILED = "failed"
    TRANSITION = "transition"
    FAULT_ARMED = "fault_armed"
    CLEARED = "cleared"


class CommandRecord(BaseModel):
    command_id: str
    status: CommandStatus
    worklist: Worklist
    result: dict[str, Any] | None
    created_at: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    command_id   TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    worklist_json TEXT NOT NULL,
    result_json  TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    command_id TEXT,
    detail     TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _record(row: sqlite3.Row) -> CommandRecord:
    return CommandRecord(
        command_id=row["command_id"],
        status=CommandStatus(row["status"]),
        worklist=Worklist.model_validate_json(row["worklist_json"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        created_at=row["created_at"],
    )


class Ledger:
    def __init__(self, path: str) -> None:
        # One connection, used only from the API's event-loop thread.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def get_command(self, command_id: str) -> CommandRecord | None:
        row = self._db.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        return _record(row) if row else None

    def start_command(self, worklist: Worklist) -> None:
        """Record the command as in_progress. Call this BEFORE the device runs."""
        with self._db:
            self._db.execute(
                "INSERT INTO commands (command_id, status, worklist_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    worklist.command_id,
                    CommandStatus.IN_PROGRESS.value,
                    worklist.model_dump_json(),
                    _now(),
                ),
            )

    def finish_command(
        self, command_id: str, status: CommandStatus, result: dict[str, Any]
    ) -> None:
        with self._db:
            self._db.execute(
                "UPDATE commands SET status = ?, result_json = ? WHERE command_id = ?",
                (status.value, json.dumps(result), command_id),
            )

    def dest_volumes(self) -> dict[tuple[str, str], float]:
        """Volume in each destination well: done commands plus completed part of failed ones."""
        totals: dict[tuple[str, str], float] = {}
        rows = self._db.execute(
            "SELECT * FROM commands WHERE status IN (?, ?)",
            (CommandStatus.DONE.value, CommandStatus.FAILED.value),
        ).fetchall()
        for row in rows:
            record = _record(row)
            if record.status is CommandStatus.DONE:
                completed = len(record.worklist.transfers)
            else:
                completed = int((record.result or {}).get("transfers_completed", 0))
            for transfer in record.worklist.transfers[:completed]:
                key = (record.worklist.dest_plate, transfer.dest_well)
                totals[key] = totals.get(key, 0.0) + transfer.volume_ul
        return totals

    def add_event(self, kind: EventKind, command_id: str | None = None, detail: str = "") -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO events (at, kind, command_id, detail) VALUES (?, ?, ?, ?)",
                (_now(), kind.value, command_id, detail),
            )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, at, kind, command_id, detail FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_ledger.py -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: pass and clean.

- [ ] **Step 5: Build log, then commit**

```bash
git add -A
git commit -m "F1: SQLite command ledger and event log

Red run: ModuleNotFoundError for labdemo.ledger. command_id is the primary key and is
recorded as in_progress before execution; dest volumes derive from done + completed-part-of-failed."
```

---

## Task 5: Orchestrator API (A1, V1, F1, F3)

**Files:**
- Create: `src/labdemo/api.py`, `tests/test_api.py`
- Modify: `README.md` (Quickstart for the API only)

**Interfaces:**
- Consumes: everything from Tasks 1–4; `spy` and `client` fixtures from `tests/conftest.py`.
- Produces: `create_app(db_path: str | None = None, simulator: Simulator | None = None) -> FastAPI`. Endpoints exactly as in the spec §5.4. Run with `uvicorn labdemo.api:create_app --factory`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api.py`. Tests are grouped by scenario so each group can be made to pass in turn (A1, then V1, F1, F3).

```python
import pytest


def body(command_id: str, *transfers: tuple[str, str, float], dest: str = "P1") -> dict:
    return {
        "command_id": command_id,
        "source_plate": "SRC1",
        "dest_plate": dest,
        "transfers": [{"source_well": s, "dest_well": d, "volume_ul": v} for s, d, v in transfers],
    }


def kinds(client) -> list[str]:
    return [event["kind"] for event in client.get("/log").json()["events"]]


# --- A1 ---------------------------------------------------------------------------------


@pytest.mark.spec("A1")
def test_valid_worklist_executes(client, spy):
    response = client.post("/commands", json=body("cmd-41", ("A1", "A1", 50.0), ("B1", "B1", 50.0)))

    assert response.status_code == 200
    assert response.json() == {
        "command_id": "cmd-41",
        "status": "done",
        "duplicate": False,
        "result": {"transfers_completed": 2},
    }
    assert len(spy.calls) == 2
    device = client.get("/device").json()
    assert device["state"] == "IDLE"
    assert device["armed_fault"] is False
    assert device["since"]

    events = client.get("/log").json()["events"]
    assert events[0]["at"] >= events[-1]["at"]  # newest first
    details = " ".join(e["detail"] for e in events if e["kind"] == "transition")
    assert "IDLE -> BUSY" in details
    assert "BUSY -> IDLE" in details
    assert {"submitted", "done"} <= set(kinds(client))


# --- V1 ---------------------------------------------------------------------------------


@pytest.mark.spec("V1")
@pytest.mark.parametrize(
    ("request_body", "expected"),
    [
        (body("bad-1", ("I1", "A1", 50.0)), "transfers[0].source_well 'I1' is not a valid well"),
        (body("bad-2", ("A1", "A1", 250.0)), "transfers[0].volume_ul=250 exceeds max 200 µL"),
        (body("bad-3", ("A1", "A1", 50.0), dest="NOPE"), "dest_plate 'NOPE' is not a known"),
        (
            body("bad-4", ("A1", "A1", 200.0), ("B1", "A1", 101.0)),
            "would hold 301 µL, exceeding max 300 µL",
        ),
    ],
)
def test_invalid_worklist_never_reaches_device(client, spy, request_body, expected):
    response = client.post("/commands", json=request_body)

    assert response.status_code == 422
    assert any(expected in error for error in response.json()["errors"])
    assert spy.calls == []
    assert client.get("/device").json()["state"] == "IDLE"
    assert "rejected" in kinds(client)


@pytest.mark.spec("V1")
def test_malformed_body_gets_the_same_error_format(client, spy):
    response = client.post("/commands", json={"command_id": "x"})

    assert response.status_code == 422
    assert isinstance(response.json()["errors"], list)
    assert spy.calls == []


@pytest.mark.spec("V1")
def test_rejected_command_is_not_recorded(client, spy):
    assert client.post("/commands", json=body("cmd-r", ("A1", "A1", 250.0))).status_code == 422

    retry = client.post("/commands", json=body("cmd-r", ("A1", "A1", 50.0)))

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False
    assert len(spy.calls) == 1


@pytest.mark.spec("V1")
def test_volume_from_earlier_commands_counts_toward_the_well_limit(client, spy):
    assert client.post("/commands", json=body("c-1", ("A1", "A1", 200.0))).status_code == 200

    response = client.post("/commands", json=body("c-2", ("B1", "A1", 101.0)))

    assert response.status_code == 422
    assert any("would hold 301" in e for e in response.json()["errors"])
    assert len(spy.calls) == 1


# --- F1 ---------------------------------------------------------------------------------


@pytest.mark.spec("F1")
def test_duplicate_command_not_reexecuted(client, spy):
    request_body = body("cmd-42", ("A1", "A1", 50.0))
    first = client.post("/commands", json=request_body)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/commands", json=request_body)

    assert second.status_code == 200
    assert second.json() == {
        "command_id": "cmd-42",
        "status": "done",
        "duplicate": True,
        "result": {"transfers_completed": 1},
    }
    assert len(spy.calls) == 1
    assert "duplicate" in kinds(client)


@pytest.mark.spec("F1")
def test_resend_of_a_well_filling_command_is_a_duplicate_not_an_overfill(client, spy):
    # A1 ends at 250 uL. Validating the resend first would count it again (500 > 300).
    request_body = body("cmd-big", ("A1", "A1", 200.0), ("B1", "A1", 50.0))
    assert client.post("/commands", json=request_body).status_code == 200

    again = client.post("/commands", json=request_body)

    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert len(spy.calls) == 2


@pytest.mark.spec("F1")
def test_reusing_a_command_id_with_a_different_worklist_is_rejected(client, spy):
    assert client.post("/commands", json=body("cmd-7", ("A1", "A1", 50.0))).status_code == 200

    response = client.post("/commands", json=body("cmd-7", ("B1", "B1", 60.0)))

    assert response.status_code == 409
    assert "different worklist" in response.json()["detail"]
    assert len(spy.calls) == 1
    assert "rejected" in kinds(client)


# --- F3 ---------------------------------------------------------------------------------


@pytest.mark.spec("F3")
def test_fault_locks_device_until_cleared(client, spy):
    assert client.post("/device/fault").json() == {"armed_fault": True}
    assert client.get("/device").json()["armed_fault"] is True

    failed = client.post(
        "/commands",
        json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0), ("C1", "C1", 50.0)),
    )
    assert failed.status_code == 500
    assert failed.json()["status"] == "failed"
    assert failed.json()["transfers_completed"] == 1
    assert client.get("/device").json()["state"] == "NEEDS_HUMAN"

    refused = client.post("/commands", json=body("cmd-next", ("D1", "D1", 10.0)))
    assert refused.status_code == 409
    assert refused.json()["state"] == "NEEDS_HUMAN"
    assert len(spy.calls) == 1
    assert "refused" in kinds(client)

    resend = client.post(
        "/commands",
        json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0), ("C1", "C1", 50.0)),
    )
    assert resend.status_code == 200
    assert resend.json()["duplicate"] is True
    assert resend.json()["status"] == "failed"
    assert len(spy.calls) == 1  # a failed command is never retried

    cleared = client.post("/device/clear", json={"operator": "stevin", "note": "checked deck"})
    assert cleared.status_code == 200
    assert cleared.json() == {"state": "IDLE"}
    assert "cleared" in kinds(client)

    # The 409 was not recorded, so the same command_id works now.
    retry = client.post("/commands", json=body("cmd-next", ("D1", "D1", 10.0)))
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False


@pytest.mark.spec("F3")
def test_partial_run_volumes_are_remembered(client):
    client.post("/device/fault")
    client.post("/commands", json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0)))
    client.post("/device/clear", json={"operator": "stevin"})

    response = client.post("/commands", json=body("cmd-g", ("C1", "A1", 150.0)))

    assert response.status_code == 422  # the first transfer of cmd-f really put 200 uL in A1
    assert any("would hold 350" in e for e in response.json()["errors"])


@pytest.mark.spec("F3")
def test_clear_is_refused_when_nothing_needs_clearing(client):
    response = client.post("/device/clear", json={"operator": "stevin"})

    assert response.status_code == 409
    assert client.get("/device").json()["state"] == "IDLE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api.py -q`
Expected: every test errors with `ModuleNotFoundError: No module named 'labdemo.api'` (raised by the `client` fixture).

- [ ] **Step 3: Implement `api.py`**

Create `src/labdemo/api.py`:

```python
"""Orchestrator API. Order of checks in POST /commands: ledger, validation, state, execute."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from labdemo.device import Device, DeviceState, ExecutionFailed, IllegalTransition, Simulator
from labdemo.ledger import CommandStatus, EventKind, Ledger
from labdemo.models import Worklist
from labdemo.simulator import PyLabRobotSimulator
from labdemo.validation import validate


class ClearRequest(BaseModel):
    operator: str = Field(min_length=1)
    note: str = ""


def create_app(db_path: str | None = None, simulator: Simulator | None = None) -> FastAPI:
    ledger = Ledger(db_path or os.environ.get("LABDEMO_DB", "./labdemo.db"))

    def log_transition(old: DeviceState, new: DeviceState, reason: str) -> None:
        ledger.add_event(EventKind.TRANSITION, detail=f"{old} -> {new}: {reason}")

    device = Device(simulator or PyLabRobotSimulator(), on_transition=log_transition)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await device.connect()
        yield
        await device.shutdown()
        ledger.close()

    app = FastAPI(title="labdemo orchestrator (simulated)", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def malformed_body(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()]
        ledger.add_event(EventKind.REJECTED, detail="; ".join(errors))
        return JSONResponse(status_code=422, content={"errors": errors})

    @app.post("/commands")
    async def submit(worklist: Worklist) -> JSONResponse:
        command_id = worklist.command_id

        # 1. Ledger lookup first: a resend must be a duplicate even if it filled a well.
        existing = ledger.get_command(command_id)
        if existing is not None:
            if existing.worklist != worklist:
                detail = "command_id was already used with a different worklist"
                ledger.add_event(EventKind.REJECTED, command_id, detail)
                return JSONResponse(status_code=409, content={"detail": detail})
            ledger.add_event(
                EventKind.DUPLICATE, command_id, f"resend of {existing.status} command"
            )
            return JSONResponse(
                content={
                    "command_id": command_id,
                    "status": existing.status.value,
                    "duplicate": True,
                    "result": existing.result,
                }
            )

        # 2. Validate. The device is never touched on failure.
        errors = validate(worklist, ledger.dest_volumes())
        if errors:
            ledger.add_event(EventKind.REJECTED, command_id, "; ".join(errors))
            return JSONResponse(status_code=422, content={"errors": errors})

        # 3. State check. Not recorded, so the client may resend this command_id later.
        if device.state is not DeviceState.IDLE:
            ledger.add_event(EventKind.REFUSED, command_id, f"device is {device.state}")
            detail = f"device is {device.state}, not IDLE"
            return JSONResponse(
                status_code=409, content={"detail": detail, "state": device.state.value}
            )

        # 4. Record before executing, so a lost reply can never cause a second run.
        ledger.start_command(worklist)
        ledger.add_event(EventKind.SUBMITTED, command_id, f"{len(worklist.transfers)} transfers")

        # 5. Execute.
        try:
            completed = await device.execute(worklist)
        except ExecutionFailed as exc:
            result = {"error": str(exc), "transfers_completed": exc.transfers_completed}
            ledger.finish_command(command_id, CommandStatus.FAILED, result)
            ledger.add_event(EventKind.FAILED, command_id, str(exc))
            return JSONResponse(
                status_code=500,
                content={"command_id": command_id, "status": "failed", **result},
            )
        result = {"transfers_completed": completed}
        ledger.finish_command(command_id, CommandStatus.DONE, result)
        ledger.add_event(EventKind.DONE, command_id, f"{completed} transfers")
        return JSONResponse(
            content={
                "command_id": command_id,
                "status": "done",
                "duplicate": False,
                "result": result,
            }
        )

    @app.get("/device")
    async def get_device() -> dict[str, object]:
        return {
            "state": device.state.value,
            "since": device.since,
            "armed_fault": device.armed_fault,
        }

    @app.get("/log")
    async def get_log(limit: int = 100) -> dict[str, object]:
        return {"events": ledger.events(max(1, min(limit, 500)))}

    @app.post("/device/fault")
    async def arm_fault() -> dict[str, bool]:
        device.arm_fault()
        ledger.add_event(
            EventKind.FAULT_ARMED, detail="fault will fire after the next first transfer"
        )
        return {"armed_fault": True}

    @app.post("/device/clear")
    async def clear(request: ClearRequest) -> JSONResponse:
        reason = f"cleared by {request.operator}" + (f": {request.note}" if request.note else "")
        try:
            device.clear(reason)
        except IllegalTransition as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        ledger.add_event(EventKind.CLEARED, detail=f"{request.operator}: {request.note}")
        return JSONResponse(content={"state": device.state.value})

    return app
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_api.py -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: pass and clean. Fix any E501 (line over 100) by wrapping the string.

- [ ] **Step 5: Smoke-test the real server once**

Run in one terminal: `uv run uvicorn labdemo.api:create_app --factory`
In another: `curl http://127.0.0.1:8000/device`
Expected: JSON with `"state":"IDLE"`. Stop the server and delete `labdemo.db`.

- [ ] **Step 6: Add the API Quickstart to the README**

In `README.md`, replace the "Running the demo" placeholder with a heading and these commands (the full section is finished in Task 7):

````markdown
## Running the demo

Start the API (an empty `labdemo.db` is created on first run):

```
uv run uvicorn labdemo.api:create_app --factory
```

Open <http://127.0.0.1:8000/docs> for the interactive API.
````

- [ ] **Step 7: Build log, then commit**

```bash
git add -A
git commit -m "A1, V1, F1, F3: orchestrator API

Red run: every test errored on ModuleNotFoundError for labdemo.api. Looks up command_id
before validating (D8) so a resend of a well-filling command is a duplicate, not an overfill."
```

---

## Task 6: AI draft gate (AI1)

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add python-dotenv`)
- Create: `src/labdemo/ai_draft.py`, `tests/test_ai_gate.py`, `recordings/overdose-250ul.json`, `recordings/column-1-50ul.json`

**Interfaces:**
- Consumes: `Worklist`, `Transfer` from `models.py`; `validate` from `validation.py`.
- Produces in `ai_draft.py`:
  - `DraftWorklist` (pydantic: `source_plate`, `dest_plate`, `transfers`): the shape the model must return. It has no `command_id`.
  - `load_recording(name_or_path: str) -> dict` (a bare name resolves to `recordings/<name>.json`), `save_recording(name: str, recording: dict) -> Path`, `draft_live(request: str) -> dict`.
  - `review(recording: dict, command_id: str) -> tuple[Worklist | None, list[str]]`.
  - `run(recording, *, command_id, approve: Callable[[Worklist], bool], submit: Callable[[Worklist], httpx.Response], out: Callable[[str], None] = print) -> int`. Exit codes: `0` submitted OK, `1` invalid (approval not offered), `2` not approved, `3` API error.
  - `main(argv: list[str] | None = None) -> int`.
- Recording format: `{"request": str, "model": str, "origin": "live" | "hand-written", "tool_input": {...}}`.

- [ ] **Step 1: Add the dependency and the hand-written fixture**

Run: `uv add python-dotenv`

Create `recordings/overdose-250ul.json`. It is a **hand-written** fixture showing what a bad model answer looks like; it is labeled as such:

```json
{
  "request": "Move 250 uL from SRC1 A1 to P1 A1",
  "model": "claude-sonnet-5-5",
  "origin": "hand-written",
  "tool_input": {
    "source_plate": "SRC1",
    "dest_plate": "P1",
    "transfers": [{"source_well": "A1", "dest_well": "A1", "volume_ul": 250}]
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ai_gate.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from labdemo.ai_draft import load_recording, run

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"


def never(_):
    raise AssertionError("must not be called")


def lines_of(recording, **kwargs):
    output: list[str] = []
    code = run(recording, command_id="ai-test", out=output.append, **kwargs)
    return code, output


@pytest.mark.spec("AI1")
def test_invalid_llm_worklist_not_offered_for_approval():
    recording = load_recording(str(RECORDINGS / "overdose-250ul.json"))

    code, output = lines_of(recording, approve=never, submit=never)

    assert code == 1
    assert any("exceeds max 200 µL" in line for line in output)
    assert any("recorded response" in line for line in output)
    assert any("approval not offered" in line.lower() for line in output)


@pytest.mark.spec("AI1")
def test_malformed_model_output_is_rejected_without_approval():
    recording = {"request": "x", "model": "m", "origin": "hand-written", "tool_input": {"a": 1}}

    code, output = lines_of(recording, approve=never, submit=never)

    assert code == 1
    assert any("does not match the worklist schema" in line for line in output)


@pytest.mark.spec("AI1")
def test_the_model_cannot_choose_the_command_id():
    recording = load_recording("column-1-50ul")
    recording["tool_input"]["command_id"] = "chosen-by-the-model"
    submitted = []

    def submit(worklist):
        submitted.append(worklist)
        return httpx.Response(200, json={"status": "done"})

    code, _ = lines_of(recording, approve=lambda _: True, submit=submit)

    assert code == 0
    assert [w.command_id for w in submitted] == ["ai-test"]


@pytest.mark.spec("AI1")
def test_declined_worklist_is_never_submitted():
    recording = load_recording("column-1-50ul")

    code, output = lines_of(recording, approve=lambda _: False, submit=never)

    assert code == 2
    assert any("Nothing was sent" in line for line in output)


@pytest.mark.spec("AI1")
def test_api_rejection_is_reported():
    recording = load_recording("column-1-50ul")

    code, output = lines_of(
        recording,
        approve=lambda _: True,
        submit=lambda _: httpx.Response(422, json={"errors": ["boom"]}),
    )

    assert code == 3
    assert any("422" in line for line in output)


def test_recordings_have_the_documented_shape():
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"request", "model", "origin", "tool_input"}
        assert data["origin"] in {"live", "hand-written"}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ai_gate.py -q`
Expected: `ModuleNotFoundError: No module named 'labdemo.ai_draft'`.

- [ ] **Step 4: Implement `ai_draft.py`**

Create `src/labdemo/ai_draft.py`:

```python
"""AI gate: an LLM drafts a worklist, the same validator checks it, a human approves it.

The model proposes; code and a person decide. Usage:
    uv run python -m labdemo.ai_draft "Transfer 50 uL from SRC1 A1-H1 into P1 column 1"
    uv run python -m labdemo.ai_draft --replay column-1-50ul      # offline, recorded response
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from labdemo.models import (
    MAX_VOLUME_UL,
    MAX_WELL_VOLUME_UL,
    MIN_VOLUME_UL,
    Transfer,
    Worklist,
)
from labdemo.validation import validate

MODEL = "claude-sonnet-5-5"
RECORDINGS_DIR = Path("recordings")

SYSTEM_PROMPT = f"""You draft liquid-handler worklists for a simulated lab.
Plates: source plate SRC1 and destination plate P1, both 96-well (rows A-H, columns 1-12).
Limits: each transfer is {MIN_VOLUME_UL:g}-{MAX_VOLUME_UL:g} uL;
a destination well holds at most {MAX_WELL_VOLUME_UL:g} uL in total.
Call propose_worklist exactly once. Do not invent plates or wells."""


class DraftWorklist(BaseModel):
    """What the model must return. There is deliberately no command_id: code assigns it."""

    source_plate: str
    dest_plate: str
    transfers: list[Transfer]


DRAFT_TOOL: dict[str, Any] = {
    "name": "propose_worklist",
    "description": "Propose a worklist of liquid transfers for human review.",
    "input_schema": DraftWorklist.model_json_schema(),
}


def _recording_path(name_or_path: str) -> Path:
    path = Path(name_or_path)
    return path if path.suffix == ".json" else RECORDINGS_DIR / f"{name_or_path}.json"


def load_recording(name_or_path: str) -> dict[str, Any]:
    return json.loads(_recording_path(name_or_path).read_text(encoding="utf-8"))


def save_recording(name: str, recording: dict[str, Any]) -> Path:
    path = _recording_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8")
    return path


def draft_live(request: str) -> dict[str, Any]:
    """Call the model once, forcing it to answer through the propose_worklist tool."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[DRAFT_TOOL],  # ty: ignore[invalid-argument-type]
        tool_choice={"type": "tool", "name": "propose_worklist"},
        messages=[{"role": "user", "content": request}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return {
                "request": request,
                "model": MODEL,
                "origin": "live",
                "tool_input": dict(block.input),  # ty: ignore[invalid-argument-type]
            }
    raise RuntimeError("the model did not call propose_worklist")


def review(recording: dict[str, Any], command_id: str) -> tuple[Worklist | None, list[str]]:
    """Parse the model's answer and run the same validate() the API uses."""
    try:
        draft = DraftWorklist.model_validate(recording["tool_input"])
    except ValidationError as exc:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return None, ["model output does not match the worklist schema", *problems]
    worklist = Worklist(command_id=command_id, **draft.model_dump())
    return worklist, validate(worklist)


def run(
    recording: dict[str, Any],
    *,
    command_id: str,
    approve: Callable[[Worklist], bool],
    submit: Callable[[Worklist], httpx.Response],
    out: Callable[[str], None] = print,
) -> int:
    origin = recording.get("origin", "unknown")
    out(f"[recorded response - origin: {origin} - model: {recording.get('model')}]")
    out(f"Request: {recording.get('request')}")

    worklist, errors = review(recording, command_id)
    if worklist is None or errors:
        out("Validation FAILED, approval not offered:")
        for error in errors:
            out(f"  - {error}")
        return 1

    out(f"Proposed worklist {worklist.command_id}:")
    out(f"  plates: {worklist.source_plate} -> {worklist.dest_plate}")
    for i, t in enumerate(worklist.transfers):
        out(f"  {i}: {t.source_well} -> {t.dest_well}  {t.volume_ul:g} µL")
    if not approve(worklist):
        out("Not approved. Nothing was sent.")
        return 2

    response = submit(worklist)
    out(f"API responded {response.status_code}: {response.text}")
    return 0 if response.is_success else 3


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Draft a worklist with an LLM, then approve it.")
    parser.add_argument("request", nargs="?", help="plain-English request (live mode)")
    parser.add_argument("--replay", metavar="NAME", help="use recordings/NAME.json, no network")
    parser.add_argument("--record", metavar="NAME", help="save the live response as NAME")
    args = parser.parse_args(argv)

    if args.replay:
        recording = load_recording(args.replay)
    elif args.request:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set. Use --replay NAME for the offline demo.")
            return 4
        recording = draft_live(args.request)
        if args.record:
            print(f"Saved {save_recording(args.record, recording)}")
    else:
        parser.error("give a request, or --replay NAME")

    api_url = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    def approve(_: Worklist) -> bool:
        return input("Approve and submit? [y/N] ").strip().lower() == "y"

    def submit(worklist: Worklist) -> httpx.Response:
        return httpx.post(f"{api_url}/commands", json=worklist.model_dump(), timeout=30)

    return run(recording, command_id=f"ai-{stamp}", approve=approve, submit=submit)


if __name__ == "__main__":
    sys.exit(main())
```

If ty reports the `# ty: ignore[...]` comments as unused or the rule name differs, run `uv run ty check` and use the rule it names, or type `DRAFT_TOOL` as `anthropic.types.ToolParam` and remove the ignores. Keep the narrowest fix.

- [ ] **Step 5: Create the valid recording**

Preferred (needs `ANTHROPIC_API_KEY` in `.env`), a genuine live recording:

```bash
uv run python -m labdemo.ai_draft --record column-1-50ul "Transfer 50 uL from SRC1 wells A1 to H1 into P1 wells A1 to H1, one transfer per well"
```

At the prompt answer `n` (the API need not be running). Open `recordings/column-1-50ul.json` and check it has 8 transfers of 50 µL and `"origin": "live"`. If the model's answer is not what the test needs, re-run once with a clearer request.

Fallback if there is no API key: create the file by hand with `"origin": "hand-written"` and these eight transfers, `A1→A1` through `H1→H1`, each `"volume_ul": 50`, with `"request": "Transfer 50 uL from SRC1 wells A1 to H1 into P1 wells A1 to H1, one transfer per well"` and `"model": "claude-sonnet-5-5"`. Say in the build log that it is hand-written.

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_ai_gate.py -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: pass and clean.

- [ ] **Step 7: Check offline replay by hand**

Run: `uv run python -m labdemo.ai_draft --replay overdose-250ul`
Expected: prints `Validation FAILED, approval not offered`, the 250 µL error, and exits with code 1. No network used.

- [ ] **Step 8: Build log, then commit**

```bash
git add -A
git commit -m "AI1: LLM drafts, validator checks, human approves

Red run: ModuleNotFoundError for labdemo.ai_draft. The model cannot choose command_id;
invalid or malformed output never reaches the approval prompt or the API."
```

---

## Task 7: Dashboard, demo script and run documentation

**Files:**
- Create: `dashboard.py`, `demo.py`, `tests/test_dashboard.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the HTTP API (`GET /device`, `GET /log`, `POST /commands`, `POST /device/fault`, `POST /device/clear`).
- Produces: a Streamlit page and a demo script, both reading `LABDEMO_API_URL`.

- [ ] **Step 1: Write the failing dashboard test**

Create `tests/test_dashboard.py`:

```python
from pathlib import Path

from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.py"


def test_dashboard_shows_a_banner_when_the_api_is_unreachable(monkeypatch):
    monkeypatch.setenv("LABDEMO_API_URL", "http://127.0.0.1:9")

    app = AppTest.from_file(str(DASHBOARD)).run()

    assert not app.exception
    assert len(app.error) == 1
    assert "Cannot reach the API at http://127.0.0.1:9" in app.error[0].value
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -q`
Expected: FAIL, `dashboard.py` does not exist.

- [ ] **Step 3: Implement `dashboard.py`**

Create `dashboard.py` (repo root):

```python
"""Read-only status page: device state and the event log. Simulator and synthetic data only."""

import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
STATE_COLORS = {
    "IDLE": "green",
    "BUSY": "orange",
    "OFFLINE": "gray",
    "ERROR": "red",
    "UNKNOWN_OUTCOME": "red",
    "NEEDS_HUMAN": "red",
}
ROW_COLORS = {
    "rejected": "#f8d7da",
    "refused": "#f8d7da",
    "failed": "#f8d7da",
    "duplicate": "#fff3cd",
}

st.set_page_config(page_title="labdemo status", layout="wide")
st.title("Simulated lab: device status")
st.caption("Simulator and synthetic data only. Not a real instrument.")
st.button("Refresh")

try:
    device = httpx.get(f"{API_URL}/device", timeout=5).json()
    events = httpx.get(f"{API_URL}/log", params={"limit": 200}, timeout=5).json()["events"]
except (httpx.HTTPError, ValueError, KeyError) as exc:
    st.error(f"Cannot reach the API at {API_URL}: {exc}")
    st.stop()

state = device["state"]
st.markdown(f"### Device: :{STATE_COLORS.get(state, 'gray')}[{state}]")
st.write(f"In this state since {device['since']}. Fault armed: {device['armed_fault']}.")

st.subheader("Event log (newest first)")
frame = pd.DataFrame(events, columns=["at", "kind", "command_id", "detail"])


def highlight(row: pd.Series) -> list[str]:
    colour = ROW_COLORS.get(row["kind"], "")
    return [f"background-color: {colour}" if colour else ""] * len(row)


st.dataframe(frame.style.apply(highlight, axis=1), hide_index=True, width="stretch")
```

If `width="stretch"` is rejected by the installed Streamlit, use `use_container_width=True`.

- [ ] **Step 4: Implement `demo.py`**

Create `demo.py` (repo root):

```python
"""Scripted run through A1 -> V1 -> F1 -> F3 against a running API (simulator, synthetic data).

Start the API first. To reset between runs, stop the API and delete labdemo.db: destination wells
fill up, and after about six runs the A1 step is rejected as an overfill.
"""

import os
import sys
import time

import httpx

API_URL = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
RUN = time.strftime("%Y%m%dT%H%M%S")
failures: list[str] = []


def worklist(suffix: str, transfers: list[tuple[str, str, float]]) -> dict:
    return {
        "command_id": f"demo-{RUN}-{suffix}",
        "source_plate": "SRC1",
        "dest_plate": "P1",
        "transfers": [{"source_well": s, "dest_well": d, "volume_ul": v} for s, d, v in transfers],
    }


def show(title: str, response: httpx.Response, expected: int) -> None:
    print(f"\n=== {title} ===")
    print(f"HTTP {response.status_code} (expected {expected})")
    print(response.text)
    if response.status_code != expected:
        failures.append(title)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with httpx.Client(base_url=API_URL, timeout=30) as http:
        try:
            state = http.get("/device").json()["state"]
        except httpx.HTTPError as exc:
            print(f"Cannot reach the API at {API_URL}: {exc}")
            return 1
        if state != "IDLE":
            print(f"Device is {state}. Clear it or restart the API first.")
            return 1

        column_1 = [(f"{row}1", f"{row}1", 50.0) for row in "ABCDEFGH"]
        a1 = worklist("a1", column_1)
        show("A1: valid worklist runs", http.post("/commands", json=a1), 200)
        show(
            "V1: invalid well and 250 uL rejected, device never contacted",
            http.post("/commands", json=worklist("v1", [("I1", "A2", 250.0)])),
            422,
        )
        show(
            "F1: resend of the A1 command is a duplicate, no second run",
            http.post("/commands", json=a1),
            200,
        )

        http.post("/device/fault")
        column_2 = [(f"{row}1", f"{row}2", 50.0) for row in "ABC"]
        show(
            "F3: injected fault fails the run part-way",
            http.post("/commands", json=worklist("f3", column_2)),
            500,
        )
        show("F3: device state", http.get("/device"), 200)
        show(
            "F3: new command refused while NEEDS_HUMAN",
            http.post("/commands", json=worklist("next", column_1[:1])),
            409,
        )
        show(
            "F3: human clears the device",
            http.post("/device/clear", json={"operator": "demo", "note": "checked the deck"}),
            200,
        )

    print(
        "\nDone. Open the dashboard to see the event log."
        if not failures
        else f"\nUnexpected: {failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

Run `uv run ruff format` after creating it; long lines get wrapped. If ty flags the `# type: ignore`, replace it with `# ty: ignore[unresolved-attribute]` or guard with `if hasattr(sys.stdout, "reconfigure")`.

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest -q && uv run ruff format && uv run ruff check && uv run ty check`
Expected: everything passes except the traceability test, which does not exist yet.

- [ ] **Step 6: Run the real demo end to end**

Terminal 1: `uv run uvicorn labdemo.api:create_app --factory`
Terminal 2: `uv run streamlit run dashboard.py`
Terminal 3: `uv run python demo.py`
Expected: demo prints `HTTP` lines matching the expected codes and ends with `Done.` Refresh the dashboard: the state is `IDLE`, and rows for `rejected`, `duplicate`, `refused` and `failed` are highlighted. Take screenshots for the slide. Stop everything and delete `labdemo.db`.

- [ ] **Step 7: Write the full README**

Replace the body of `README.md` below the "How this repo was built" section so it contains, in this order, with real commands (copy from the sections above):

1. **Prerequisites**: uv, optional `ANTHROPIC_API_KEY` (only to re-record).
2. **Quickstart**: `uv sync`, then the API, dashboard and `demo.py` commands in three terminals, plus "delete `labdemo.db` to reset".
3. **Scenarios**: a table with columns ID, how to trigger it (`demo.py` step name or curl), what you see on the dashboard. Rows A1, V1, F1, F3 and AI1 (`uv run python -m labdemo.ai_draft --replay overdose-250ul`, which is rejected, and `--replay column-1-50ul`, which asks for approval and needs the API running).
4. **AI step**: replay is offline; re-recording needs `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`); recordings are labeled with their origin.
5. **Development**: `uv sync`, `uv run pre-commit install`, `uv run pytest`, `uv run ruff check`, `uv run ty check`.
6. **Project layout**: one line per file, taken from the spec §4 table.
7. **Sustainability** ("built in a weekend, still maintainable"), one bullet each:
   - Quality gates from the first commit: pre-commit (ruff, ty) and CI on every push and PR.
   - Tests that document behavior: each acceptance scenario is a named test with an ID, and a traceability test fails CI if the spec and the tests drift apart.
   - Reproducible environment: `uv.lock` is committed and the Python version is pinned, so a fresh clone is one command.
   - Clear boundaries: validation is a pure function, the state machine is a small table, and the simulator sits behind the `Simulator` protocol, so a vendor SDK replaces one class.
   - Recorded decisions: `docs/decisions.md` says what was chosen, why, and when to revisit it; `BUILD_LOG.md` records what the AI got wrong.
   - Stated limitations and next steps (the next section).
8. **Known limitations and what I'd do next**: spec §11 in order, plus: device state is not persisted across an API restart; `in_progress` commands left by a crash are never retried and need a human.
9. **Troubleshooting**: port 8000 in use (`--port 8001` and set `LABDEMO_API_URL`); dashboard banner "Cannot reach the API"; missing API key; PyLabRobot prints each operation to the console (that is the simulator, not an error).

Change the **Status** line to say the implementation is complete and tested.

- [ ] **Step 8: Build log, then commit**

```bash
git add -A
git commit -m "Add dashboard, demo script and run documentation

Red run: the dashboard test failed because dashboard.py did not exist. Demo run end to end
against the real API; dashboard highlights rejected, duplicate, refused and failed events."
```

---

## Task 8: Traceability, CI and pull request

**Files:**
- Create: `tests/test_traceability.py`
- Modify: `docs/specs/2026-09-30-simulated-lab-integration.md` (status), `README.md` (if links changed)

**Interfaces:**
- Consumes: the `spec` markers on every test, and the scenario table in the spec.

- [ ] **Step 1: Write the traceability test**

Create `tests/test_traceability.py`:

```python
"""Fails if a scenario ID in the spec has no test, or a test names an ID the spec lacks."""

import re
from pathlib import Path

SPECS = Path(__file__).resolve().parent.parent / "docs" / "specs"
ID_ROW = re.compile(r"^\| ([A-Z]{1,2}[0-9]+) \|", re.MULTILINE)


def spec_ids() -> set[str]:
    return {i for path in SPECS.glob("*.md") for i in ID_ROW.findall(path.read_text("utf-8"))}


def test_every_scenario_has_a_test_and_every_marker_a_scenario(request):
    covered = {
        marker.args[0] for item in request.session.items for marker in item.iter_markers("spec")
    }
    ids = spec_ids()
    assert ids == {"A1", "V1", "F1", "F3", "AI1"}, "scenario table in the spec changed"
    assert ids - covered == set(), f"scenarios with no test: {sorted(ids - covered)}"
    assert covered - ids == set(), f"markers naming unknown scenarios: {sorted(covered - ids)}"
```

Run it with the full suite (it reads the markers of every collected test): `uv run pytest -q`.
Expected: pass. Temporarily rename one marker to `"F9"` to see it fail with a readable message, then undo.

- [ ] **Step 2: Run the whole gate exactly as CI does**

```bash
uv sync --locked
uv run pre-commit run --all-files
uv run pytest
```

Expected: all pass. Fix anything that does not before continuing.

- [ ] **Step 3: Verify the README from a clean clone**

```bash
git clone . "$TEMP/labdemo-clean" && cd "$TEMP/labdemo-clean"
git checkout feature/init
```

Follow only the README's Quickstart in the clone: install, start the API, run the dashboard and `demo.py`, and the AI replay. Expected: every scenario works with no outside knowledge. Fix the README for anything that did not. Return to the repo and delete the clone.

- [ ] **Step 4: Update the spec status and commit**

In the spec header set the status line to `Implemented`. Then:

```bash
git add -A
git commit -m "Enforce spec-to-test traceability in CI and finish docs"
git push -u origin feature/init
```

- [ ] **Step 5: Open the pull request**

The `gh` CLI is not installed. Either `winget install GitHub.cli` and then run the command below, or open `https://github.com/stevin-wilson/lab-automation-demo/compare/main...feature/init` in the browser and paste the same body.

```bash
gh pr create --base main --head feature/init --title "Simulated lab integration (A1, V1, F1, F3, AI1)" --body-file pr-body.md
```

The body must: link the spec and plan, list the five scenario IDs with the test that proves each, summarize the Review Focus items and what was found, and note any task where the build log records the AI getting something wrong. End the body with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 6: Wait for CI, then merge**

Confirm the CI check is green on the PR, then merge into `main` (a normal merge commit keeps the per-task history).

---

## Self-Review notes

- **Spec coverage:** §3 scenarios A1/V1/F1/F3/AI1 → Tasks 1, 3, 4, 5, 6 with markers; §4 modules → Tasks 1–6 plus `dashboard.py`/`demo.py` in Task 7; §5.1 rules → Task 1 (+ ledger-derived volumes in Task 4); §5.2 state machine → Task 3; §5.3 order and §5.4 contract → Task 5; §5.5 AI gate → Task 6; §5.6 dashboard → Task 7; §6 error handling → Tasks 3, 5, 6, 7; §7 configuration → Tasks 5, 6, 7; §8 traceability → Task 8; §9 tooling and CI → already committed, verified in Task 8; §11 cut list → README in Task 7; §12 honesty → recording origin labels, README, build log.
- **Spec drift fixed in the spec itself:** lookup-before-validate and the 409 for a reused `command_id` (D8), the `simulator.py` module, the recording `origin` field, python-dotenv, and the `--factory` run command.
- **Not tested automatically (by design):** the dashboard's happy path (verified by hand in Task 7 Step 6), live LLM calls (verified by hand in Task 6 Step 5), and `UNKNOWN_OUTCOME` (modeled in the state table only).
