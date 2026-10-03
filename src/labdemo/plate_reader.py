"""Mock plate reader: writes one synthetic absorbance read of a 96-well plate as a CSV file.

The values are seeded random numbers, not a model of the liquid in each well. Usage:
    uv run python -m labdemo.plate_reader --inbox inbox [--plate P1] [--seed 1]
"""

import argparse
import os
import random
from datetime import UTC, datetime
from pathlib import Path

from labdemo.models import COLUMNS, ROWS

SYNTHETIC_HEADER = "# SYNTHETIC DATA - mock plate reader, not a real instrument"


def write_readout(inbox: Path, plate: str = "P1", seed: int | None = None) -> Path:
    """Write a read of every well to inbox and return the file's path."""
    rng = random.Random(seed)
    lines = [SYNTHETIC_HEADER, "plate,well,od600"]
    lines += [f"{plate},{row}{col},{rng.uniform(0.04, 1.2):.4f}" for row in ROWS for col in COLUMNS]

    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = inbox / f"{plate}_{stamp}.csv"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    # The rename is atomic, so the watcher never picks up a half-written .csv.
    os.replace(temp, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", type=Path, default=Path("inbox"))
    parser.add_argument("--plate", default="P1")
    parser.add_argument("--seed", type=int, help="same seed, same file bytes (a duplicate)")
    args = parser.parse_args()
    print(f"Wrote synthetic read: {write_readout(args.inbox, args.plate, args.seed)}")


if __name__ == "__main__":
    main()
