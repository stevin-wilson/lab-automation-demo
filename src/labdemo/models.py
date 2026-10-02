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
