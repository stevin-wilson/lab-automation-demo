import re
import runpy
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.py"


def test_dashboard_shows_a_banner_when_the_api_is_unreachable(monkeypatch):
    monkeypatch.setenv("LABDEMO_API_URL", "http://127.0.0.1:9")

    app = AppTest.from_file(str(DASHBOARD)).run(timeout=30)

    assert not app.exception
    assert len(app.error) == 1
    assert "Cannot reach the API at http://127.0.0.1:9" in app.error[0].value


def test_dashboard_reads_the_api_url_from_a_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "LABDEMO_API_URL", "placeholder"
    )  # registers cleanup of what load_dotenv sets
    monkeypatch.delenv("LABDEMO_API_URL")
    (tmp_path / ".env").write_text("LABDEMO_API_URL=http://127.0.0.1:9\n")
    script = tmp_path / "dashboard.py"
    script.write_text(DASHBOARD.read_text(encoding="utf-8"), encoding="utf-8")

    app = AppTest.from_file(str(script)).run(timeout=30)

    assert not app.exception
    assert "Cannot reach the API at http://127.0.0.1:9" in app.error[0].value


def test_demo_reads_the_api_url_from_a_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LABDEMO_API_URL", "placeholder")
    monkeypatch.delenv("LABDEMO_API_URL")
    (tmp_path / ".env").write_text("LABDEMO_API_URL=http://127.0.0.1:9\n")
    script = tmp_path / "demo.py"
    script.write_text(DASHBOARD.with_name("demo.py").read_text(encoding="utf-8"), encoding="utf-8")

    namespace = runpy.run_path(str(script), run_name="demo_under_test")

    assert namespace["API_URL"] == "http://127.0.0.1:9"


def test_dashboard_shows_the_time_since_the_last_transition(monkeypatch):
    since = (datetime.now(UTC) - timedelta(seconds=125)).isoformat(timespec="milliseconds")
    answers = {
        "/device": {"state": "IDLE", "since": since, "armed_fault": False},
        "/log": {"events": []},
    }

    def fake_get(url, **_kwargs):
        path = "/log" if url.endswith("/log") else "/device"
        return types.SimpleNamespace(json=lambda: answers[path])

    monkeypatch.setattr(httpx, "get", fake_get)

    app = AppTest.from_file(str(DASHBOARD)).run(timeout=30)

    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert re.search(rf"In this state for 2 min \d+ s, since {re.escape(since)}", text)


def test_demo_checks_the_response_body_not_just_the_status(monkeypatch, tmp_path):
    monkeypatch.setenv("LABDEMO_API_URL", "http://127.0.0.1:9")
    script = tmp_path / "demo.py"
    script.write_text(DASHBOARD.with_name("demo.py").read_text(encoding="utf-8"), encoding="utf-8")
    namespace = runpy.run_path(str(script), run_name="demo_under_test")
    show, failures = namespace["show"], namespace["failures"]

    show("ok", httpx.Response(200, json={"duplicate": True}), 200, lambda b: b["duplicate"] is True)
    show(
        "wrong body", httpx.Response(200, json={"duplicate": False}), 200, lambda b: b["duplicate"]
    )
    show("wrong code", httpx.Response(500, json={}), 200)

    assert failures == ["wrong body", "wrong code"]


def test_demo_passes_end_to_end_against_the_real_app(monkeypatch, tmp_path, spy):
    from fastapi.testclient import TestClient

    from labdemo.api import create_app

    script = tmp_path / "demo.py"
    script.write_text(DASHBOARD.with_name("demo.py").read_text(encoding="utf-8"), encoding="utf-8")
    namespace = runpy.run_path(str(script), run_name="demo_under_test")
    app = create_app(str(tmp_path / "demo.db"), spy)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: TestClient(app))

    assert namespace["main"]() == 0
    assert namespace["failures"] == []
