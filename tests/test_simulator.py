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
