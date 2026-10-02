import pytest


def body(command_id: str, *transfers: tuple[str, str, float], dest: str = "P1") -> dict:
    return {
        "command_id": command_id,
        "source_plate": "SRC1",
        "dest_plate": dest,
        "transfers": [{"source_well": s, "dest_well": d, "volume_ul": v} for s, d, v in transfers],
    }


def kinds(client) -> list[str]:
    return [event["kind"] for event in client.get("/log").json()["events"]]


# --- A1 ---------------------------------------------------------------------------------


@pytest.mark.spec("A1")
def test_valid_worklist_executes(client, spy):
    response = client.post("/commands", json=body("cmd-41", ("A1", "A1", 50.0), ("B1", "B1", 50.0)))

    assert response.status_code == 200
    assert response.json() == {
        "command_id": "cmd-41",
        "status": "done",
        "duplicate": False,
        "result": {"transfers_completed": 2},
    }
    assert len(spy.calls) == 2
    device = client.get("/device").json()
    assert device["state"] == "IDLE"
    assert device["armed_fault"] is False
    assert device["since"]

    events = client.get("/log").json()["events"]
    assert events[0]["at"] >= events[-1]["at"]  # newest first
    details = " ".join(e["detail"] for e in events if e["kind"] == "transition")
    assert "IDLE -> BUSY" in details
    assert "BUSY -> IDLE" in details
    assert {"submitted", "done"} <= set(kinds(client))


# --- V1 ---------------------------------------------------------------------------------


@pytest.mark.spec("V1")
@pytest.mark.parametrize(
    ("request_body", "expected"),
    [
        (body("bad-1", ("I1", "A1", 50.0)), "transfers[0].source_well 'I1' is not a valid well"),
        (body("bad-2", ("A1", "A1", 250.0)), "transfers[0].volume_ul=250 exceeds max 200 µL"),
        (body("bad-3", ("A1", "A1", 50.0), dest="NOPE"), "dest_plate 'NOPE' is not a known"),
        (
            body("bad-4", ("A1", "A1", 200.0), ("B1", "A1", 101.0)),
            "would hold 301 µL, exceeding max 300 µL",
        ),
    ],
)
def test_invalid_worklist_never_reaches_device(client, spy, request_body, expected):
    response = client.post("/commands", json=request_body)

    assert response.status_code == 422
    assert any(expected in error for error in response.json()["errors"])
    assert spy.calls == []
    assert client.get("/device").json()["state"] == "IDLE"
    assert "rejected" in kinds(client)


@pytest.mark.spec("V1")
def test_malformed_body_gets_the_same_error_format(client, spy):
    response = client.post("/commands", json={"command_id": "x"})

    assert response.status_code == 422
    assert isinstance(response.json()["errors"], list)
    assert spy.calls == []


@pytest.mark.spec("V1")
def test_rejected_command_is_not_recorded(client, spy):
    assert client.post("/commands", json=body("cmd-r", ("A1", "A1", 250.0))).status_code == 422

    retry = client.post("/commands", json=body("cmd-r", ("A1", "A1", 50.0)))

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False
    assert len(spy.calls) == 1


@pytest.mark.spec("V1")
def test_volume_from_earlier_commands_counts_toward_the_well_limit(client, spy):
    assert client.post("/commands", json=body("c-1", ("A1", "A1", 200.0))).status_code == 200

    response = client.post("/commands", json=body("c-2", ("B1", "A1", 101.0)))

    assert response.status_code == 422
    assert any("would hold 301" in e for e in response.json()["errors"])
    assert len(spy.calls) == 1


# --- F1 ---------------------------------------------------------------------------------


@pytest.mark.spec("F1")
def test_duplicate_command_not_reexecuted(client, spy):
    request_body = body("cmd-42", ("A1", "A1", 50.0))
    first = client.post("/commands", json=request_body)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/commands", json=request_body)

    assert second.status_code == 200
    assert second.json() == {
        "command_id": "cmd-42",
        "status": "done",
        "duplicate": True,
        "result": {"transfers_completed": 1},
    }
    assert len(spy.calls) == 1
    assert "duplicate" in kinds(client)


@pytest.mark.spec("F1")
def test_resend_of_a_well_filling_command_is_a_duplicate_not_an_overfill(client, spy):
    # A1 ends at 250 uL. Validating the resend first would count it again (500 > 300).
    request_body = body("cmd-big", ("A1", "A1", 200.0), ("B1", "A1", 50.0))
    assert client.post("/commands", json=request_body).status_code == 200

    again = client.post("/commands", json=request_body)

    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert len(spy.calls) == 2


@pytest.mark.spec("F1")
def test_reusing_a_command_id_with_a_different_worklist_is_rejected(client, spy):
    assert client.post("/commands", json=body("cmd-7", ("A1", "A1", 50.0))).status_code == 200

    response = client.post("/commands", json=body("cmd-7", ("B1", "B1", 60.0)))

    assert response.status_code == 409
    assert "different worklist" in response.json()["detail"]
    assert len(spy.calls) == 1
    assert "rejected" in kinds(client)


# --- F3 ---------------------------------------------------------------------------------


@pytest.mark.spec("F3")
def test_fault_locks_device_until_cleared(client, spy):
    assert client.post("/device/fault").json() == {"armed_fault": True}
    assert client.get("/device").json()["armed_fault"] is True

    failed = client.post(
        "/commands",
        json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0), ("C1", "C1", 50.0)),
    )
    assert failed.status_code == 500
    assert failed.json()["status"] == "failed"
    assert failed.json()["transfers_completed"] == 1
    assert client.get("/device").json()["state"] == "NEEDS_HUMAN"

    refused = client.post("/commands", json=body("cmd-next", ("D1", "D1", 10.0)))
    assert refused.status_code == 409
    assert refused.json()["state"] == "NEEDS_HUMAN"
    assert len(spy.calls) == 1
    assert "refused" in kinds(client)

    resend = client.post(
        "/commands",
        json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0), ("C1", "C1", 50.0)),
    )
    assert resend.status_code == 200
    assert resend.json()["duplicate"] is True
    assert resend.json()["status"] == "failed"
    assert len(spy.calls) == 1  # a failed command is never retried

    cleared = client.post("/device/clear", json={"operator": "stevin", "note": "checked deck"})
    assert cleared.status_code == 200
    assert cleared.json() == {"state": "IDLE"}
    assert "cleared" in kinds(client)

    # The 409 was not recorded, so the same command_id works now.
    retry = client.post("/commands", json=body("cmd-next", ("D1", "D1", 10.0)))
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False


@pytest.mark.spec("F3")
def test_partial_run_volumes_are_remembered(client):
    client.post("/device/fault")
    client.post("/commands", json=body("cmd-f", ("A1", "A1", 200.0), ("B1", "B1", 50.0)))
    client.post("/device/clear", json={"operator": "stevin"})

    response = client.post("/commands", json=body("cmd-g", ("C1", "A1", 150.0)))

    assert response.status_code == 422  # the first transfer of cmd-f really put 200 uL in A1
    assert any("would hold 350" in e for e in response.json()["errors"])


@pytest.mark.spec("F3")
def test_clear_is_refused_when_nothing_needs_clearing(client):
    response = client.post("/device/clear", json={"operator": "stevin"})

    assert response.status_code == 409
    assert client.get("/device").json()["state"] == "IDLE"
