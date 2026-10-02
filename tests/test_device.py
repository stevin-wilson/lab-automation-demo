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
