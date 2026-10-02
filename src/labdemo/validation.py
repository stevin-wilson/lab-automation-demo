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
