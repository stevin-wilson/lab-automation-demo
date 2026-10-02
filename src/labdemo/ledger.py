"""SQLite command ledger (the idempotency key) and event log."""

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from labdemo.models import Worklist


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


class CommandRecord(BaseModel):
    command_id: str
    status: CommandStatus
    worklist: Worklist
    result: dict[str, Any] | None
    created_at: str


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

    def dest_volumes(self) -> dict[tuple[str, str], float]:
        """Volume in each destination well: done commands plus completed part of failed ones."""
        totals: dict[tuple[str, str], float] = {}
        rows = self._db.execute(
            "SELECT * FROM commands WHERE status IN (?, ?)",
            (CommandStatus.DONE.value, CommandStatus.FAILED.value),
        ).fetchall()
        for row in rows:
            record = _record(row)
            if record.status is CommandStatus.DONE:
                completed = len(record.worklist.transfers)
            else:
                completed = int((record.result or {}).get("transfers_completed", 0))
            for transfer in record.worklist.transfers[:completed]:
                key = (record.worklist.dest_plate, transfer.dest_well)
                totals[key] = totals.get(key, 0.0) + transfer.volume_ul
        return totals

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
