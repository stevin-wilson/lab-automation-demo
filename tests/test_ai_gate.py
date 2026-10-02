import json
import types
from pathlib import Path

import anthropic
import httpx
import httpx2
import pytest

from labdemo import ai_draft
from labdemo.ai_draft import load_recording, main, run

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


@pytest.fixture
def live_mode(monkeypatch):
    monkeypatch.setattr(ai_draft, "load_dotenv", lambda: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def failing_draft(error):
    def draft(_request):
        raise error

    return draft


@pytest.mark.spec("AI1")
def test_unexpected_model_answer_points_to_replay(live_mode, monkeypatch, capsys):
    monkeypatch.setattr(ai_draft, "draft_live", failing_draft(RuntimeError("boom")))

    assert main(["a request"]) == 4

    assert "--replay" in capsys.readouterr().out


@pytest.mark.spec("AI1")
def test_anthropic_api_error_points_to_replay(live_mode, monkeypatch, capsys):
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "http://example.invalid"))
    monkeypatch.setattr(ai_draft, "draft_live", failing_draft(error))

    assert main(["a request"]) == 4

    output = capsys.readouterr().out
    assert "--replay" in output
    assert "Connection error" in output


@pytest.mark.spec("AI1")
def test_missing_api_key_points_to_replay(monkeypatch, capsys):
    monkeypatch.setattr(ai_draft, "load_dotenv", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert main(["a request"]) == 4

    assert "--replay" in capsys.readouterr().out


DRAFT = {
    "source_plate": "SRC1",
    "dest_plate": "P1",
    "transfers": [{"source_well": "A1", "dest_well": "A1", "volume_ul": 50}],
}


def fake_anthropic(monkeypatch, content):
    """Replace anthropic.Anthropic with a fake that records the request. No network."""
    calls: list[dict] = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(content=content)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return calls


@pytest.mark.spec("AI1")
def test_live_request_does_not_force_a_tool(monkeypatch):
    block = types.SimpleNamespace(type="tool_use", input=DRAFT)
    calls = fake_anthropic(monkeypatch, [block])

    recording = ai_draft.draft_live("move 50 uL")

    (kwargs,) = calls
    # claude-sonnet-5-5 rejects tool_choice of type "tool" or "any" with HTTP 400.
    assert kwargs.get("tool_choice", {"type": "auto"}) == {"type": "auto"}
    assert [t["name"] for t in kwargs["tools"]] == ["propose_worklist"]
    assert kwargs["max_tokens"] >= 4096
    assert "propose_worklist" in kwargs["system"]
    assert kwargs["messages"] == [{"role": "user", "content": "move 50 uL"}]
    assert recording == {
        "request": "move 50 uL",
        "model": ai_draft.MODEL,
        "origin": "live",
        "tool_input": DRAFT,
    }


@pytest.mark.spec("AI1")
def test_live_response_without_a_tool_call_raises(monkeypatch):
    fake_anthropic(monkeypatch, [types.SimpleNamespace(type="text", text="no tool")])

    with pytest.raises(RuntimeError, match="did not call propose_worklist"):
        ai_draft.draft_live("move 50 uL")


def test_recordings_have_the_documented_shape():
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"request", "model", "origin", "tool_input"}
        assert data["origin"] in {"live", "hand-written"}
