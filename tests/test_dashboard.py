import runpy
from pathlib import Path

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
