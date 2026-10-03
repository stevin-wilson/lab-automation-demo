import sqlite3

import pytest

from labdemo.ledger import CommandStatus, EventKind, Ledger
from labdemo.models import Transfer, Worklist


def worklist(command_id: str = "cmd-1", *transfers: Transfer) -> Worklist:
    default = [Transfer(source_well="A1", dest_well="A1", volume_ul=50.0)]
    return Worklist(
        command_id=command_id,
        source_plate="SRC1",
        dest_plate="P1",
        transfers=list(transfers) or default,
    )


@pytest.fixture
def ledger(tmp_path):
    db = Ledger(str(tmp_path / "ledger.db"))
    yield db
    db.close()


def test_unknown_command_is_none(ledger):
    assert ledger.get_command("nope") is None


@pytest.mark.spec("F1")
def test_command_is_recorded_as_in_progress_before_it_runs(ledger):
    ledger.start_command(worklist("cmd-42"))
    record = ledger.get_command("cmd-42")
    assert record is not None
    assert record.status is CommandStatus.IN_PROGRESS
    assert record.worklist == worklist("cmd-42")
    assert record.result is None


@pytest.mark.spec("F1")
def test_command_id_is_the_primary_key(ledger):
    ledger.start_command(worklist("cmd-42"))
    with pytest.raises(sqlite3.IntegrityError):
        ledger.start_command(worklist("cmd-42"))


@pytest.mark.spec("F1")
def test_records_survive_reopening_the_database(tmp_path):
    path = str(tmp_path / "ledger.db")
    first = Ledger(path)
    first.start_command(worklist("cmd-42"))
    first.finish_command("cmd-42", CommandStatus.DONE, {"transfers_completed": 1})
    first.close()

    second = Ledger(path)
    record = second.get_command("cmd-42")
    second.close()
    assert record is not None
    assert record.status is CommandStatus.DONE
    assert record.result == {"transfers_completed": 1}


def test_dest_volumes_count_done_and_completed_part_of_failed(ledger):
    done = worklist(
        "done",
        Transfer(source_well="A1", dest_well="A1", volume_ul=100.0),
        Transfer(source_well="B1", dest_well="A1", volume_ul=50.0),
    )
    failed = worklist(
        "failed",
        Transfer(source_well="A1", dest_well="B1", volume_ul=20.0),
        Transfer(source_well="B1", dest_well="B1", volume_ul=30.0),
    )
    running = worklist("running", Transfer(source_well="A1", dest_well="C1", volume_ul=99.0))
    for item in (done, failed, running):
        ledger.start_command(item)
    ledger.finish_command("done", CommandStatus.DONE, {"transfers_completed": 2})
    ledger.finish_command("failed", CommandStatus.FAILED, {"transfers_completed": 1})

    assert ledger.dest_volumes() == {("P1", "A1"): 150.0, ("P1", "B1"): 20.0}


@pytest.mark.spec("R1")
def test_well_commands_follow_the_same_completed_transfers_as_volumes(ledger):
    first = worklist("first", Transfer(source_well="A1", dest_well="A1", volume_ul=10.0))
    second = worklist(
        "second",
        Transfer(source_well="A1", dest_well="A1", volume_ul=10.0),
        Transfer(source_well="B1", dest_well="B1", volume_ul=10.0),
    )
    running = worklist("running", Transfer(source_well="A1", dest_well="C1", volume_ul=10.0))
    for item in (first, second, running):
        ledger.start_command(item)
    ledger.finish_command("first", CommandStatus.DONE, {"transfers_completed": 1})
    ledger.finish_command("second", CommandStatus.FAILED, {"transfers_completed": 1})

    assert ledger.well_commands() == {("P1", "A1"): ["first", "second"]}


def test_events_are_newest_first_and_limited(ledger):
    ledger.add_event(EventKind.SUBMITTED, "cmd-1", "first")
    ledger.add_event(EventKind.DONE, "cmd-1", "second")
    ledger.add_event(EventKind.CLEARED, None, "third")

    events = ledger.events(limit=2)
    assert [e["detail"] for e in events] == ["third", "second"]
    assert [e["kind"] for e in events] == ["cleared", "done"]
    assert events[0]["command_id"] is None
    assert set(events[0]) == {"id", "at", "kind", "command_id", "detail"}
