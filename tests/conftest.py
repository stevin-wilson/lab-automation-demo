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
    from labdemo.api import create_app  # ty: ignore[unresolved-import]  # api.py lands in Task 3

    with TestClient(create_app(str(tmp_path / "test.db"), spy)) as test_client:
        yield test_client
