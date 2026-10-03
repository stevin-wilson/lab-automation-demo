"""Orchestrator API. Order of checks in POST /commands: ledger, validation, state, execute.

POST /readouts follows the same order without the device: ledger, validation, record.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from labdemo.device import Device, DeviceState, ExecutionFailed, IllegalTransition, Simulator
from labdemo.ledger import CommandStatus, EventKind, Ledger
from labdemo.models import Readout, Worklist
from labdemo.simulator import PyLabRobotSimulator
from labdemo.validation import validate, validate_readout


class ClearRequest(BaseModel):
    operator: str = Field(min_length=1)
    note: str = ""


def create_app(db_path: str | None = None, simulator: Simulator | None = None) -> FastAPI:
    load_dotenv()
    ledger = Ledger(db_path or os.environ.get("LABDEMO_DB", "./labdemo.db"))

    def log_transition(old: DeviceState, new: DeviceState, reason: str) -> None:
        ledger.add_event(EventKind.TRANSITION, detail=f"{old} -> {new}: {reason}")

    device = Device(simulator or PyLabRobotSimulator(), on_transition=log_transition)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await device.connect()
        yield
        await device.shutdown()
        ledger.close()

    app = FastAPI(title="labdemo orchestrator (simulated)", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def malformed_body(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()]
        ledger.add_event(EventKind.REJECTED, detail="; ".join(errors))
        return JSONResponse(status_code=422, content={"errors": errors})

    @app.post("/commands")
    async def submit(worklist: Worklist) -> JSONResponse:
        command_id = worklist.command_id

        # 1. Ledger lookup first: a resend must be a duplicate even if it filled a well.
        existing = ledger.get_command(command_id)
        if existing is not None:
            if existing.worklist != worklist:
                detail = "command_id was already used with a different worklist"
                ledger.add_event(EventKind.REJECTED, command_id, detail)
                return JSONResponse(status_code=409, content={"detail": detail})
            ledger.add_event(
                EventKind.DUPLICATE, command_id, f"resend of {existing.status} command"
            )
            return JSONResponse(
                content={
                    "command_id": command_id,
                    "status": existing.status.value,
                    "duplicate": True,
                    "result": existing.result,
                }
            )

        # 2. Validate. The device is never touched on failure.
        errors = validate(worklist, ledger.dest_volumes())
        if errors:
            ledger.add_event(EventKind.REJECTED, command_id, "; ".join(errors))
            return JSONResponse(status_code=422, content={"errors": errors})

        # 3. State check. Not recorded, so the client may resend this command_id later.
        if device.state is not DeviceState.IDLE:
            ledger.add_event(EventKind.REFUSED, command_id, f"device is {device.state}")
            detail = f"device is {device.state}, not IDLE"
            return JSONResponse(
                status_code=409, content={"detail": detail, "state": device.state.value}
            )

        # 4. Record before executing, so a lost reply can never cause a second run.
        ledger.start_command(worklist)
        ledger.add_event(EventKind.SUBMITTED, command_id, f"{len(worklist.transfers)} transfers")

        # 5. Execute.
        try:
            completed = await device.execute(worklist)
        except ExecutionFailed as exc:
            result = {"error": str(exc), "transfers_completed": exc.transfers_completed}
            ledger.finish_command(command_id, CommandStatus.FAILED, result)
            ledger.add_event(EventKind.FAILED, command_id, str(exc))
            return JSONResponse(
                status_code=500,
                content={"command_id": command_id, "status": "failed", **result},
            )
        result = {"transfers_completed": completed}
        ledger.finish_command(command_id, CommandStatus.DONE, result)
        ledger.add_event(EventKind.DONE, command_id, f"{completed} transfers")
        return JSONResponse(
            content={
                "command_id": command_id,
                "status": "done",
                "duplicate": False,
                "result": result,
            }
        )

    @app.get("/device")
    async def get_device() -> dict[str, object]:
        return {
            "state": device.state.value,
            "since": device.since,
            "armed_fault": device.armed_fault,
        }

    @app.get("/log")
    async def get_log(limit: int = 100) -> dict[str, object]:
        return {"events": ledger.events(max(1, min(limit, 500)))}

    @app.post("/device/fault")
    async def arm_fault() -> dict[str, bool]:
        device.arm_fault()
        ledger.add_event(
            EventKind.FAULT_ARMED, detail="fault will fire after the next first transfer"
        )
        return {"armed_fault": True}

    @app.post("/device/clear")
    async def clear(request: ClearRequest) -> JSONResponse:
        reason = f"cleared by {request.operator}" + (f": {request.note}" if request.note else "")
        try:
            device.clear(reason)
        except IllegalTransition as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        ledger.add_event(EventKind.CLEARED, detail=reason)
        return JSONResponse(content={"state": device.state.value})

    @app.post("/readouts")
    async def ingest_readout(readout: Readout) -> JSONResponse:
        readout_id = readout.readout_id
        label = f"readout {readout_id[:12]}"

        # 1. Ledger lookup first: the same file sent again is a duplicate, never stored twice.
        existing = ledger.get_readout(readout_id)
        if existing is not None:
            # source_file is not compared: the same bytes re-exported under a new name are a resend.
            same = (existing.readout.plate, existing.readout.readings) == (
                readout.plate,
                readout.readings,
            )
            if not same:
                detail = "readout_id was already used with a different readout"
                ledger.add_event(EventKind.REJECTED, detail=f"{label}: {detail}")
                return JSONResponse(status_code=409, content={"detail": detail})
            ledger.add_event(EventKind.DUPLICATE, detail=f"{label}: resend of a stored readout")
            return JSONResponse(
                content={"readout_id": readout_id, "duplicate": True, "lineage": existing.lineage}
            )

        # 2. Validate. Nothing is stored on failure.
        errors = validate_readout(readout)
        if errors:
            ledger.add_event(EventKind.REJECTED, detail=f"{label}: " + "; ".join(errors))
            return JSONResponse(status_code=422, content={"errors": errors})

        # 3. Snapshot each well's lineage as of now: its volume and the commands that filled it.
        volumes = ledger.dest_volumes()
        sources = ledger.well_commands()
        lineage = [
            {
                "well": reading.well,
                "value": reading.value,
                "volume_ul": volumes.get((readout.plate, reading.well), 0.0),
                "command_ids": sources.get((readout.plate, reading.well), []),
            }
            for reading in readout.readings
        ]
        ledger.add_readout(readout, lineage)
        traced = sum(1 for well in lineage if well["command_ids"])
        ledger.add_event(
            EventKind.READOUT,
            detail=f"{label}: {readout.plate}, {len(lineage)} wells, {traced} traced to commands",
        )
        return JSONResponse(
            content={"readout_id": readout_id, "duplicate": False, "lineage": lineage}
        )

    @app.get("/readouts/{readout_id}")
    async def get_readout(readout_id: str) -> JSONResponse:
        record = ledger.get_readout(readout_id)
        if record is None:
            return JSONResponse(status_code=404, content={"detail": "unknown readout_id"})
        return JSONResponse(
            content={
                "readout_id": readout_id,
                "plate": record.readout.plate,
                "source_file": record.readout.source_file,
                "ingested_at": record.ingested_at,
                "lineage": record.lineage,
            }
        )

    return app
