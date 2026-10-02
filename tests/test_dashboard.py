from pathlib import Path

from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.py"


def test_dashboard_shows_a_banner_when_the_api_is_unreachable(monkeypatch):
    monkeypatch.setenv("LABDEMO_API_URL", "http://127.0.0.1:9")

    app = AppTest.from_file(str(DASHBOARD)).run(timeout=30)

    assert not app.exception
    assert len(app.error) == 1
    assert "Cannot reach the API at http://127.0.0.1:9" in app.error[0].value
