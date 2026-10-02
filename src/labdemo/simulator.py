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
