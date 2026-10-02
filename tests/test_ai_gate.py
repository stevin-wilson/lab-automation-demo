import json
from pathlib import Path

import httpx
import pytest

from labdemo.ai_draft import load_recording, run

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"


def never(_):
    raise AssertionError("must not be called")


def lines_of(recording, **kwargs):
    output: list[str] = []
    code = run(recording, command_id="ai-test", out=output.append, **kwargs)
    return code, output


@pytest.mark.spec("AI1")
def test_invalid_llm_worklist_not_offered_for_approval():
    recording = load_recording(str(RECORDINGS / "overdose-250ul.json"))

    code, output = lines_of(recording, approve=never, submit=never)

    assert code == 1
    assert any("exceeds max 200 µL" in line for line in output)
    assert any("recorded response" in line for line in output)
    assert any("approval not offered" in line.lower() for line in output)


@pytest.mark.spec("AI1")
def test_malformed_model_output_is_rejected_without_approval():
    recording = {"request": "x", "model": "m", "origin": "hand-written", "tool_input": {"a": 1}}

    code, output = lines_of(recording, approve=never, submit=never)

    assert code == 1
    assert any("does not match the worklist schema" in line for line in output)


@pytest.mark.spec("AI1")
def test_the_model_cannot_choose_the_command_id():
    recording = load_recording("column-1-50ul")
    recording["tool_input"]["command_id"] = "chosen-by-the-model"
    submitted = []

    def submit(worklist):
        submitted.append(worklist)
        return httpx.Response(200, json={"status": "done"})

    code, _ = lines_of(recording, approve=lambda _: True, submit=submit)

    assert code == 0
    assert [w.command_id for w in submitted] == ["ai-test"]


@pytest.mark.spec("AI1")
def test_declined_worklist_is_never_submitted():
    recording = load_recording("column-1-50ul")

    code, output = lines_of(recording, approve=lambda _: False, submit=never)

    assert code == 2
    assert any("Nothing was sent" in line for line in output)


@pytest.mark.spec("AI1")
def test_api_rejection_is_reported():
    recording = load_recording("column-1-50ul")

    code, output = lines_of(
        recording,
        approve=lambda _: True,
        submit=lambda _: httpx.Response(422, json={"errors": ["boom"]}),
    )

    assert code == 3
    assert any("422" in line for line in output)


def test_recordings_have_the_documented_shape():
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"request", "model", "origin", "tool_input"}
        assert data["origin"] in {"live", "hand-written"}
