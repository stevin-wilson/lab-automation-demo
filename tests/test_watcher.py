"""R1-R3: the mock plate reader, the inbox watcher and per-well lineage. Synthetic data only."""

from pathlib import Path

import httpx
import pytest

from labdemo.plate_reader import SYNTHETIC_HEADER, write_readout
from labdemo.watcher import exit_code, parse_csv, process_inbox


def worklist(command_id: str, *transfers: tuple[str, str, float]) -> dict:
    return {
        "command_id": command_id,
        "source_plate": "SRC1",
        "dest_plate": "P1",
        "transfers": [{"source_well": s, "dest_well": d, "volume_ul": v} for s, d, v in transfers],
    }


def kinds(client) -> list[str]:
    return [event["kind"] for event in client.get("/log").json()["events"]]


def write_csv(inbox: Path, name: str, *lines: str) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def readout_body(readout_id: str, value: float) -> dict:
    return {
        "readout_id": readout_id,
        "plate": "P1",
        "source_file": "manual.csv",
        "readings": [{"well": "A1", "value": value}],
    }


# --- Mock plate reader ------------------------------------------------------------------


def test_mock_reader_writes_a_labelled_96_well_file_and_no_temp_file(tmp_path):
    path = write_readout(tmp_path / "inbox", seed=1)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == SYNTHETIC_HEADER
    assert lines[1] == "plate,well,od600"
    assert len(lines) == 2 + 96
    assert lines[2].startswith("P1,A1,")
    assert list((tmp_path / "inbox").glob("*.tmp")) == []


def test_mock_reader_is_deterministic_for_a_seed(tmp_path):
    first = write_readout(tmp_path / "a", seed=3)
    second = write_readout(tmp_path / "b", seed=3)

    assert first.read_bytes() == second.read_bytes()


# --- R1 ---------------------------------------------------------------------------------


@pytest.mark.spec("R1")
def test_readout_is_ingested_with_per_well_lineage(client, spy, tmp_path):
    column_1 = [(f"{row}1", f"{row}1", 50.0) for row in "ABCDEFGH"]
    assert client.post("/commands", json=worklist("cmd-col1", *column_1)).status_code == 200
    inbox = tmp_path / "inbox"
    path = write_readout(inbox, seed=1)

    (outcome,) = process_inbox(inbox, client)

    assert outcome.status == "ingested"
    assert "96 wells, 8 traced to commands" in outcome.message
    assert not path.exists()
    processed = inbox / "processed" / path.name
    assert processed.exists()

    stored = client.get(f"/readouts/{parse_csv(processed).readout_id}")
    assert stored.status_code == 200
    assert stored.json()["plate"] == "P1"
    assert stored.json()["source_file"] == path.name
    lineage = {well["well"]: well for well in stored.json()["lineage"]}
    assert len(lineage) == 96
    assert lineage["A1"]["command_ids"] == ["cmd-col1"]
    assert lineage["H1"]["volume_ul"] == 50.0
    assert lineage["A2"]["command_ids"] == []  # a reading no command explains
    assert lineage["A2"]["volume_ul"] == 0.0
    assert "readout" in kinds(client)
    assert len(spy.calls) == 8  # reading results never touches the liquid handler
    assert client.get("/device").json()["state"] == "IDLE"


@pytest.mark.spec("R1")
def test_lineage_counts_only_the_completed_part_of_a_failed_command(client, tmp_path):
    client.post("/device/fault")
    failed = client.post(
        "/commands", json=worklist("cmd-f", ("A1", "A1", 50.0), ("B1", "B1", 50.0))
    )
    assert failed.status_code == 500
    inbox = tmp_path / "inbox"
    path = write_readout(inbox, seed=1)

    (outcome,) = process_inbox(inbox, client)

    readout_id = parse_csv(inbox / "processed" / path.name).readout_id
    lineage = {w["well"]: w for w in client.get(f"/readouts/{readout_id}").json()["lineage"]}
    assert outcome.status == "ingested"
    assert lineage["A1"]["command_ids"] == ["cmd-f"]
    assert lineage["B1"]["command_ids"] == []  # the fault fired before B1 was dispensed


@pytest.mark.spec("R1")
def test_lineage_lists_every_command_that_filled_a_well(client):
    assert client.post("/commands", json=worklist("c-1", ("A1", "A1", 50.0))).status_code == 200
    assert client.post("/commands", json=worklist("c-2", ("B1", "A1", 20.0))).status_code == 200

    response = client.post("/readouts", json=readout_body("r-1", 0.5))

    assert response.status_code == 200
    (well,) = response.json()["lineage"]
    assert well == {"well": "A1", "value": 0.5, "volume_ul": 70.0, "command_ids": ["c-1", "c-2"]}


@pytest.mark.spec("R1")
def test_unknown_readout_is_404(client):
    assert client.get("/readouts/nope").status_code == 404


# --- R2 ---------------------------------------------------------------------------------


@pytest.mark.spec("R2")
def test_invalid_readout_is_rejected_and_not_stored(client, spy, tmp_path):
    inbox = tmp_path / "inbox"
    write_csv(inbox, "bad.csv", "plate,well,od600", "P1,I1,0.5", "P1,A1,0.2", "P1,A1,0.3")

    (outcome,) = process_inbox(inbox, client)

    assert outcome.status == "rejected"
    assert "readings[0].well 'I1' is not a valid well (A1-H12)" in outcome.message
    assert "readings[2].well 'A1' appears more than once" in outcome.message
    rejected = inbox / "rejected" / "bad.csv"
    assert rejected.exists()
    assert "'I1'" in (inbox / "rejected" / "bad.csv.error.txt").read_text(encoding="utf-8")
    assert client.get(f"/readouts/{parse_csv(rejected).readout_id}").status_code == 404
    assert "rejected" in kinds(client)
    assert "readout" not in kinds(client)
    assert spy.calls == []


@pytest.mark.spec("R2")
def test_readout_for_an_unknown_plate_is_rejected(client, tmp_path):
    inbox = tmp_path / "inbox"
    write_csv(inbox, "p9.csv", "plate,well,od600", "P9,A1,0.5")

    (outcome,) = process_inbox(inbox, client)

    assert outcome.status == "rejected"
    assert "plate 'P9' is not a known destination plate" in outcome.message


@pytest.mark.spec("R2")
def test_non_finite_value_sent_directly_to_the_api_is_rejected(client):
    raw = (
        '{"readout_id": "r-nan", "plate": "P1", "source_file": "x.csv",'
        ' "readings": [{"well": "A1", "value": NaN}]}'
    )

    response = client.post("/readouts", content=raw, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert "readings[0].value must be a finite number" in response.json()["errors"]
    assert client.get("/readouts/r-nan").status_code == 404


@pytest.mark.spec("R2")
@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (("plate,well,od600", "P1,A1,high"), "'high' is not a number"),
        (("plate,well,od600", "P1,A1,nan"), "'nan' is not a finite number"),
        (("plate,well,absorbance", "P1,A1,0.5"), "header must be plate,well,od600"),
        ((), "header must be plate,well,od600"),
        (("plate,well,od600", "P1,A1,0.5", "P2,A2,0.5"), "exactly one plate"),
    ],
)
def test_unparseable_file_is_rejected_without_calling_the_api(client, tmp_path, lines, expected):
    inbox = tmp_path / "inbox"
    write_csv(inbox, "odd.csv", *lines)

    (outcome,) = process_inbox(inbox, client)

    assert outcome.status == "rejected"
    assert expected in outcome.message
    assert (inbox / "rejected" / "odd.csv").exists()
    assert (inbox / "rejected" / "odd.csv.error.txt").exists()
    assert "rejected" not in kinds(client)  # the API never saw it


# --- R3 ---------------------------------------------------------------------------------


@pytest.mark.spec("R3")
def test_same_file_again_is_a_duplicate_not_stored_twice(client, tmp_path):
    inbox = tmp_path / "inbox"
    first = write_readout(inbox, seed=7)
    (ingested,) = process_inbox(inbox, client)
    # The instrument re-exports the same read under a new file name.
    (inbox / "re-export.csv").write_bytes((inbox / "processed" / first.name).read_bytes())

    (again,) = process_inbox(inbox, client)

    assert ingested.status == "ingested"
    assert again.status == "duplicate"
    assert (inbox / "processed" / "re-export.csv").exists()
    assert kinds(client).count("readout") == 1
    assert "duplicate" in kinds(client)


@pytest.mark.spec("R3")
def test_reused_readout_id_with_different_readings_is_rejected(client):
    assert client.post("/readouts", json=readout_body("r-7", 0.5)).status_code == 200

    response = client.post("/readouts", json=readout_body("r-7", 0.9))

    assert response.status_code == 409
    assert "different readout" in response.json()["detail"]
    stored = client.get("/readouts/r-7").json()
    assert stored["lineage"][0]["value"] == 0.5
    assert "rejected" in kinds(client)


@pytest.mark.spec("R3")
def test_api_unreachable_leaves_the_file_for_the_next_poll(tmp_path):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    inbox = tmp_path / "inbox"
    path = write_readout(inbox, seed=1)
    transport = httpx.MockTransport(refuse)
    with httpx.Client(base_url="http://127.0.0.1:9", transport=transport) as http:
        (outcome,) = process_inbox(inbox, http)

    assert outcome.status == "retry"
    assert path.exists()
    assert not (inbox / "processed").exists()


def test_half_written_files_are_ignored(client, tmp_path):
    inbox = tmp_path / "inbox"
    partial = write_csv(inbox, "P1_x.csv.tmp", "plate,well")

    assert process_inbox(inbox, client) == []
    assert partial.exists()


def test_exit_code_reflects_the_worst_outcome(client, tmp_path):
    inbox = tmp_path / "inbox"
    write_readout(inbox, seed=1)
    ok = process_inbox(inbox, client)
    write_csv(inbox, "bad.csv", "plate,well,od600", "P1,I1,0.5")
    bad = process_inbox(inbox, client)

    assert exit_code([]) == 0
    assert exit_code(ok) == 0
    assert exit_code(ok + bad) == 1
