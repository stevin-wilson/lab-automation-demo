"""Orchestrator API. Order of checks in POST /commands: ledger, validation, state, execute."""

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
from labdemo.models import Worklist
from labdemo.simulator import PyLabRobotSimulator
from labdemo.validation import validate


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

    return app
