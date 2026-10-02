from datetime import UTC, datetime

import pytest

from labdemo.timefmt import elapsed_since

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        ("2026-10-02T12:00:00.000+00:00", "0 s"),
        ("2026-10-02T11:59:48.000+00:00", "12 s"),
        ("2026-10-02T11:59:00.000+00:00", "1 min 0 s"),
        ("2026-10-02T11:57:35.000+00:00", "2 min 25 s"),
        ("2026-10-02T11:00:01.000+00:00", "59 min 59 s"),
        ("2026-10-02T11:00:00.000+00:00", "1 h 0 min"),
        ("2026-10-02T08:30:00.000+00:00", "3 h 30 min"),
        ("2026-10-02T12:00:30.000+00:00", "0 s"),  # in the future: clamps to zero
    ],
)
def test_elapsed_since(since, expected):
    assert elapsed_since(since, NOW) == expected
