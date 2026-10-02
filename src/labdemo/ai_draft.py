"""AI gate: an LLM drafts a worklist, the same validator checks it, a human approves it.

The model proposes; code and a person decide. Usage:
    uv run python -m labdemo.ai_draft "Transfer 50 uL from SRC1 A1-H1 into P1 column 1"
    uv run python -m labdemo.ai_draft --replay column-1-50ul      # offline, recorded response
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from labdemo.models import (
    MAX_VOLUME_UL,
    MAX_WELL_VOLUME_UL,
    MIN_VOLUME_UL,
    Transfer,
    Worklist,
)
from labdemo.validation import validate

if TYPE_CHECKING:
    from anthropic.types import ToolParam

MODEL = "claude-sonnet-5-5"
RECORDINGS_DIR = Path("recordings")

SYSTEM_PROMPT = f"""You draft liquid-handler worklists for a simulated lab.
Plates: source plate SRC1 and destination plate P1, both 96-well (rows A-H, columns 1-12).
Limits: each transfer is {MIN_VOLUME_UL:g}-{MAX_VOLUME_UL:g} uL;
a destination well holds at most {MAX_WELL_VOLUME_UL:g} uL in total.
Call propose_worklist exactly once. Do not invent plates or wells."""


class DraftWorklist(BaseModel):
    """What the model must return. There is deliberately no command_id: code assigns it."""

    source_plate: str
    dest_plate: str
    transfers: list[Transfer]


DRAFT_TOOL: "ToolParam" = {
    "name": "propose_worklist",
    "description": "Propose a worklist of liquid transfers for human review.",
    "input_schema": DraftWorklist.model_json_schema(),
}


def _recording_path(name_or_path: str) -> Path:
    path = Path(name_or_path)
    return path if path.suffix == ".json" else RECORDINGS_DIR / f"{name_or_path}.json"


def load_recording(name_or_path: str) -> dict[str, Any]:
    return json.loads(_recording_path(name_or_path).read_text(encoding="utf-8"))


def save_recording(name: str, recording: dict[str, Any]) -> Path:
    path = _recording_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8")
    return path


def draft_live(request: str) -> dict[str, Any]:
    """Call the model once, forcing it to answer through the propose_worklist tool."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "propose_worklist"},
        messages=[{"role": "user", "content": request}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return {
                "request": request,
                "model": MODEL,
                "origin": "live",
                "tool_input": dict(block.input),
            }
    raise RuntimeError("the model did not call propose_worklist")


def review(recording: dict[str, Any], command_id: str) -> tuple[Worklist | None, list[str]]:
    """Parse the model's answer and run the same validate() the API uses."""
    try:
        draft = DraftWorklist.model_validate(recording["tool_input"])
    except ValidationError as exc:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return None, ["model output does not match the worklist schema", *problems]
    worklist = Worklist(command_id=command_id, **draft.model_dump())
    return worklist, validate(worklist)


def run(
    recording: dict[str, Any],
    *,
    command_id: str,
    approve: Callable[[Worklist], bool],
    submit: Callable[[Worklist], httpx.Response],
    out: Callable[[str], None] = print,
) -> int:
    origin = recording.get("origin", "unknown")
    out(f"[recorded response - origin: {origin} - model: {recording.get('model')}]")
    out(f"Request: {recording.get('request')}")

    worklist, errors = review(recording, command_id)
    if worklist is None or errors:
        out("Validation FAILED, approval not offered:")
        for error in errors:
            out(f"  - {error}")
        return 1

    out(f"Proposed worklist {worklist.command_id}:")
    out(f"  plates: {worklist.source_plate} -> {worklist.dest_plate}")
    for i, t in enumerate(worklist.transfers):
        out(f"  {i}: {t.source_well} -> {t.dest_well}  {t.volume_ul:g} µL")
    if not approve(worklist):
        out("Not approved. Nothing was sent.")
        return 2

    response = submit(worklist)
    out(f"API responded {response.status_code}: {response.text}")
    return 0 if response.is_success else 3


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Draft a worklist with an LLM, then approve it.")
    parser.add_argument("request", nargs="?", help="plain-English request (live mode)")
    parser.add_argument("--replay", metavar="NAME", help="use recordings/NAME.json, no network")
    parser.add_argument("--record", metavar="NAME", help="save the live response as NAME")
    args = parser.parse_args(argv)

    if args.replay:
        recording = load_recording(args.replay)
    elif args.request:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set. Use --replay NAME for the offline demo.")
            return 4
        import anthropic

        try:
            recording = draft_live(args.request)
        except (anthropic.APIError, RuntimeError) as exc:
            print(f"The live draft failed: {exc}. Use --replay NAME for the offline demo.")
            return 4
        if args.record:
            print(f"Saved {save_recording(args.record, recording)}")
    else:
        parser.error("give a request, or --replay NAME")

    api_url = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    def approve(_: Worklist) -> bool:
        return input("Approve and submit? [y/N] ").strip().lower() == "y"

    def submit(worklist: Worklist) -> httpx.Response:
        return httpx.post(f"{api_url}/commands", json=worklist.model_dump(), timeout=30)

    return run(recording, command_id=f"ai-{stamp}", approve=approve, submit=submit)


if __name__ == "__main__":
    sys.exit(main())
