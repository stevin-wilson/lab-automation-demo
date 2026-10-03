"""Fails if a scenario ID in the spec has no test, or a test names an ID the spec lacks.

Only meaningful in a full-suite run: run alone or with -k it sees few markers and reports every
scenario as untested.
"""

import re
from pathlib import Path

SPECS = Path(__file__).resolve().parent.parent / "docs" / "specs"
ID_ROW = re.compile(r"^\| ([A-Z]{1,2}[0-9]+) \|", re.MULTILINE)


def spec_ids() -> set[str]:
    return {i for path in SPECS.glob("*.md") for i in ID_ROW.findall(path.read_text("utf-8"))}


def test_every_scenario_has_a_test_and_every_marker_a_scenario(request):
    covered = {
        marker.args[0] for item in request.session.items for marker in item.iter_markers("spec")
    }
    ids = spec_ids()
    assert ids == {"A1", "V1", "F1", "F3", "AI1", "R1", "R2", "R3"}, (
        "scenario table in the spec changed; if that was intentional, update the expected set here"
    )
    assert ids - covered == set(), f"scenarios with no test: {sorted(ids - covered)}"
    assert covered - ids == set(), f"markers naming unknown scenarios: {sorted(covered - ids)}"
