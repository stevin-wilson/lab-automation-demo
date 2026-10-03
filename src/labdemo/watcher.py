"""Inbox watcher: polls a folder for plate-reader CSV files and posts each one to the API.

The file's SHA-256 is the readout_id, so a file sent twice is stored once. A file the API accepts
moves to processed/, a rejected one to rejected/ with a .error.txt beside it, and a file the API
could not take stays in the inbox for the next poll. Usage:
    uv run python -m labdemo.watcher --inbox inbox            # poll until Ctrl+C
    uv run python -m labdemo.watcher --inbox inbox --once     # one pass; exit 0, 1 or 2
"""

import argparse
import csv
import hashlib
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

from labdemo.models import Reading, Readout

HEADER = ["plate", "well", "od600"]
EXIT_CODES = {"ingested": 0, "duplicate": 0, "rejected": 1, "retry": 2}


class ParseError(ValueError):
    """The file is not a readout this watcher understands, so it never reaches the API."""


@dataclass
class Outcome:
    file: str
    status: str  # ingested, duplicate, rejected or retry
    message: str


def parse_csv(path: Path) -> Readout:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"not UTF-8 text: {exc}") from exc
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames != HEADER:
        raise ParseError(f"header must be {','.join(HEADER)} (got {reader.fieldnames})")

    plates: set[str] = set()
    readings: list[Reading] = []
    for number, row in enumerate(reader, start=1):
        plates.add(row["plate"])
        raw = row["od600"]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ParseError(f"row {number}: od600 {raw!r} is not a number") from None
        if not math.isfinite(value):
            raise ParseError(f"row {number}: od600 {raw!r} is not a finite number")
        readings.append(Reading(well=row["well"], value=value))
    if len(plates) != 1:
        raise ParseError(f"expected exactly one plate per file (got {sorted(plates)})")

    return Readout(
        readout_id=hashlib.sha256(data).hexdigest(),
        plate=plates.pop(),
        source_file=path.name,
        readings=readings,
    )


def _move(path: Path, folder: str, outcome: Outcome) -> Outcome:
    target = path.parent / folder
    target.mkdir(exist_ok=True)
    os.replace(path, target / path.name)
    if folder == "rejected":
        (target / f"{path.name}.error.txt").write_text(outcome.message + "\n", encoding="utf-8")
    return outcome


def process_inbox(inbox: Path, http: httpx.Client) -> list[Outcome]:
    """One pass over inbox/*.csv. Only .csv is read, so a .csv.tmp still being written waits."""
    outcomes: list[Outcome] = []
    for path in sorted(inbox.glob("*.csv")):
        try:
            readout = parse_csv(path)
        except ParseError as exc:
            outcomes.append(_move(path, "rejected", Outcome(path.name, "rejected", str(exc))))
            continue

        try:
            response = http.post("/readouts", json=readout.model_dump())
        except httpx.TransportError as exc:
            # Leave the file: resending later is safe because the API stores a readout_id once.
            outcomes.append(Outcome(path.name, "retry", f"API unreachable, will retry: {exc}"))
            break

        if response.status_code == 200:
            body = response.json()
            traced = sum(1 for well in body["lineage"] if well["command_ids"])
            summary = (
                f"readout {readout.readout_id[:12]}: "
                f"{len(body['lineage'])} wells, {traced} traced to commands"
            )
            if body["duplicate"]:
                outcome = Outcome(
                    path.name, "duplicate", f"{summary} (duplicate, not stored again)"
                )
            else:
                outcome = Outcome(path.name, "ingested", summary)
            outcomes.append(_move(path, "processed", outcome))
        elif response.status_code in (409, 422):
            body = response.json()
            message = "; ".join(body.get("errors") or [str(body.get("detail"))])
            outcomes.append(_move(path, "rejected", Outcome(path.name, "rejected", message)))
        else:
            message = f"HTTP {response.status_code} from the API, will retry"
            outcomes.append(Outcome(path.name, "retry", message))
    return outcomes


def exit_code(outcomes: list[Outcome]) -> int:
    """0 if every file was stored or was a duplicate, 1 if any was rejected, 2 if any must retry."""
    return max((EXIT_CODES[outcome.status] for outcome in outcomes), default=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", type=Path, default=Path("inbox"))
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between polls")
    args = parser.parse_args()

    load_dotenv()
    api_url = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
    args.inbox.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=api_url, timeout=10) as http:
        if not args.once:
            print(f"Watching {args.inbox} and posting to {api_url}/readouts. Ctrl+C to stop.")
        while True:
            outcomes = process_inbox(args.inbox, http)
            for outcome in outcomes:
                print(f"{outcome.status:9} {outcome.file}: {outcome.message}", flush=True)
            if args.once:
                if not outcomes:
                    print(f"No .csv files in {args.inbox}.")
                return exit_code(outcomes)
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                return 0


if __name__ == "__main__":
    sys.exit(main())
