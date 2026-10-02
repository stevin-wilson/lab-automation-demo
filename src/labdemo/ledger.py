"""SQLite command ledger (the idempotency key), plate readouts with lineage, and event log."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from labdemo.models import Readout, Transfer, Worklist


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
    READOUT = "readout"


class CommandRecord(BaseModel):
    command_id: str
    status: CommandStatus
    worklist: Worklist
    result: dict[str, Any] | None
    created_at: str


class ReadoutRecord(BaseModel):
    readout: Readout
    lineage: list[dict[str, Any]]
    ingested_at: str


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
CREATE TABLE IF NOT EXISTS readouts (
    readout_id   TEXT PRIMARY KEY,
    plate        TEXT NOT NULL,
    readout_json TEXT NOT NULL,
    lineage_json TEXT NOT NULL,
    ingested_at  TEXT NOT NULL
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

    def _completed_transfers(self) -> Iterator[tuple[CommandRecord, Transfer]]:
        """Transfers that really ran: all of a done command, the completed part of a failed one."""
        rows = self._db.execute(
            "SELECT * FROM commands WHERE status IN (?, ?) ORDER BY created_at, command_id",
            (CommandStatus.DONE.value, CommandStatus.FAILED.value),
        ).fetchall()
        for row in rows:
            record = _record(row)
            if record.status is CommandStatus.DONE:
                completed = len(record.worklist.transfers)
            else:
                completed = int((record.result or {}).get("transfers_completed", 0))
            for transfer in record.worklist.transfers[:completed]:
                yield record, transfer

    def dest_volumes(self) -> dict[tuple[str, str], float]:
        """Volume in each destination well: done commands plus completed part of failed ones."""
        totals: dict[tuple[str, str], float] = {}
        for record, transfer in self._completed_transfers():
            key = (record.worklist.dest_plate, transfer.dest_well)
            totals[key] = totals.get(key, 0.0) + transfer.volume_ul
        return totals

    def well_commands(self) -> dict[tuple[str, str], list[str]]:
        """The command_ids that put liquid in each destination well, oldest first."""
        sources: dict[tuple[str, str], list[str]] = {}
        for record, transfer in self._completed_transfers():
            ids = sources.setdefault((record.worklist.dest_plate, transfer.dest_well), [])
            if record.command_id not in ids:
                ids.append(record.command_id)
        return sources

    def get_readout(self, readout_id: str) -> ReadoutRecord | None:
        row = self._db.execute(
            "SELECT * FROM readouts WHERE readout_id = ?", (readout_id,)
        ).fetchone()
        if row is None:
            return None
        return ReadoutRecord(
            readout=Readout.model_validate_json(row["readout_json"]),
            lineage=json.loads(row["lineage_json"]),
            ingested_at=row["ingested_at"],
        )

    def add_readout(self, readout: Readout, lineage: list[dict[str, Any]]) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO readouts (readout_id, plate, readout_json, lineage_json, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    readout.readout_id,
                    readout.plate,
                    readout.model_dump_json(),
                    json.dumps(lineage),
                    _now(),
                ),
            )

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
