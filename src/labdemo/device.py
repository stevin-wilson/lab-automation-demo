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
