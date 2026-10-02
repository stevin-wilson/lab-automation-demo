import pytest

from labdemo.models import Reading, Readout, Transfer, Worklist
from labdemo.validation import validate, validate_readout


def make(
    *transfers: Transfer,
    source: str = "SRC1",
    dest: str = "P1",
    command_id: str = "cmd-1",
) -> Worklist:
    return Worklist(
        command_id=command_id,
        source_plate=source,
        dest_plate=dest,
        transfers=list(transfers),
    )


def xfer(src: str = "A1", dst: str = "A1", vol: float = 50.0) -> Transfer:
    return Transfer(source_well=src, dest_well=dst, volume_ul=vol)


@pytest.mark.spec("V1")
def test_valid_worklist_has_no_errors():
    assert validate(make(xfer(), xfer("H12", "H12", 200.0), xfer(vol=1.0))) == []


@pytest.mark.spec("V1")
@pytest.mark.parametrize(
    ("transfer", "expected"),
    [
        (xfer(src="I1"), "transfers[0].source_well 'I1' is not a valid well (A1-H12)"),
        (xfer(dst="A13"), "transfers[0].dest_well 'A13' is not a valid well (A1-H12)"),
        (xfer(src="a1"), "transfers[0].source_well 'a1' is not a valid well (A1-H12)"),
        (xfer(dst="A1 "), "transfers[0].dest_well 'A1 ' is not a valid well (A1-H12)"),
        (xfer(vol=250.0), "transfers[0].volume_ul=250 exceeds max 200 µL"),
        (xfer(vol=0.5), "transfers[0].volume_ul=0.5 is below min 1 µL"),
        (xfer(vol=0.0), "transfers[0].volume_ul=0 is below min 1 µL"),
        (xfer(vol=-5.0), "transfers[0].volume_ul=-5 is below min 1 µL"),
        (xfer(vol=float("nan")), "transfers[0].volume_ul must be a finite number"),
        (xfer(vol=float("inf")), "transfers[0].volume_ul must be a finite number"),
    ],
)
def test_bad_transfer_is_reported(transfer, expected):
    assert expected in validate(make(transfer))


@pytest.mark.spec("V1")
def test_unknown_and_wrong_role_plates_are_reported():
    errors = validate(make(xfer(), source="P1", dest="SRC1"))
    assert "source_plate 'P1' is not a known source plate (known: SRC1)" in errors
    assert "dest_plate 'SRC1' is not a known destination plate (known: P1)" in errors


@pytest.mark.spec("V1")
def test_transfer_count_limits():
    assert "transfers must contain 1-96 items (got 0)" in validate(make())
    too_many = [xfer("A1", "A1", 1.0)] * 97
    assert "transfers must contain 1-96 items (got 97)" in validate(make(*too_many))


@pytest.mark.spec("V1")
def test_destination_overfill_within_one_worklist():
    errors = validate(make(xfer(dst="B2", vol=200.0), xfer("A1", "B2", 101.0)))
    assert errors == ["transfers[1].dest_well P1/B2 would hold 301 µL, exceeding max 300 µL"]


@pytest.mark.spec("V1")
def test_destination_overfill_counts_volume_already_in_the_well():
    errors = validate(make(xfer(dst="B2", vol=200.0)), {("P1", "B2"): 150.0})
    assert errors == ["transfers[0].dest_well P1/B2 would hold 350 µL, exceeding max 300 µL"]


@pytest.mark.spec("V1")
def test_exactly_full_well_is_allowed():
    assert validate(make(xfer(dst="B2", vol=200.0), xfer("A1", "B2", 100.0))) == []


@pytest.mark.spec("V1")
def test_all_errors_are_reported_together():
    errors = validate(make(xfer(src="I1", dst="A13", vol=250.0), source="NOPE"))
    assert len(errors) == 4


@pytest.mark.spec("V1")
def test_blank_command_id_is_reported():
    assert "command_id must not be empty" in validate(make(xfer(), command_id="  "))


# --- Readouts (R2) ----------------------------------------------------------------------


def readout(*readings: tuple[str, float], plate: str = "P1", readout_id: str = "r-1") -> Readout:
    return Readout(
        readout_id=readout_id,
        plate=plate,
        source_file="read.csv",
        readings=[Reading(well=well, value=value) for well, value in readings],
    )


@pytest.mark.spec("R2")
def test_valid_readout_has_no_errors():
    assert validate_readout(readout(("A1", 0.5), ("H12", 0.0))) == []


@pytest.mark.spec("R2")
def test_all_readout_errors_are_reported_together():
    errors = validate_readout(
        readout(("I1", 0.5), ("A1", float("inf")), ("A1", 0.2), plate="SRC1", readout_id=" ")
    )

    assert errors == [
        "readout_id must not be empty",
        "plate 'SRC1' is not a known destination plate (known: P1)",
        "readings[0].well 'I1' is not a valid well (A1-H12)",
        "readings[1].value must be a finite number",
        "readings[2].well 'A1' appears more than once",
    ]


@pytest.mark.spec("R2")
def test_readout_reading_count_limits():
    assert "readings must contain 1-96 items (got 0)" in validate_readout(readout())
    too_many = [(f"{row}{col}", 0.1) for row in "ABCDEFGH" for col in range(1, 13)] * 2
    assert any(
        "readings must contain 1-96 items" in e for e in validate_readout(readout(*too_many))
    )
