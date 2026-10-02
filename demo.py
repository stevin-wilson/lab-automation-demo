"""Scripted run through A1 -> V1 -> F1 -> F3 against a running API (simulator, synthetic data).

Start the API first. To reset between runs, stop the API and delete labdemo.db: destination wells
fill up, and after about six runs the A1 step is rejected as an overfill.
"""

import os
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

API_URL = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
RUN = time.strftime("%Y%m%dT%H%M%S")
failures: list[str] = []


def worklist(suffix: str, transfers: list[tuple[str, str, float]]) -> dict:
    return {
        "command_id": f"demo-{RUN}-{suffix}",
        "source_plate": "SRC1",
        "dest_plate": "P1",
        "transfers": [{"source_well": s, "dest_well": d, "volume_ul": v} for s, d, v in transfers],
    }


def show(
    title: str,
    response: httpx.Response,
    expected: int,
    check: Callable[[Any], bool] | None = None,
) -> None:
    """Print a step; record a failure on a wrong status code or when check(body) is not true."""
    print(f"\n=== {title} ===")
    print(f"HTTP {response.status_code} (expected {expected})")
    print(response.text)
    ok = response.status_code == expected
    if ok and check is not None:
        try:
            ok = bool(check(response.json()))
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            print("Response body was not as expected.")
    if not ok:
        failures.append(title)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # ty: ignore[unresolved-attribute]
    with httpx.Client(base_url=API_URL, timeout=30) as http:
        try:
            state = http.get("/device").json()["state"]
        except httpx.HTTPError as exc:
            print(f"Cannot reach the API at {API_URL}: {exc}")
            return 1
        if state != "IDLE":
            print(f"Device is {state}. Clear it or restart the API first.")
            return 1

        column_1 = [(f"{row}1", f"{row}1", 50.0) for row in "ABCDEFGH"]
        a1 = worklist("a1", column_1)
        show("A1: valid worklist runs", http.post("/commands", json=a1), 200)
        show(
            "V1: invalid well and 250 uL rejected, device never contacted",
            http.post("/commands", json=worklist("v1", [("I1", "A2", 250.0)])),
            422,
        )
        show(
            "F1: resend of the A1 command is a duplicate, no second run",
            http.post("/commands", json=a1),
            200,
            lambda body: body["duplicate"] is True,
        )

        http.post("/device/fault")
        column_2 = [(f"{row}1", f"{row}2", 50.0) for row in "ABC"]
        show(
            "F3: injected fault fails the run part-way",
            http.post("/commands", json=worklist("f3", column_2)),
            500,
        )
        show(
            "F3: device state is NEEDS_HUMAN",
            http.get("/device"),
            200,
            lambda body: body["state"] == "NEEDS_HUMAN",
        )
        show(
            "F3: new command refused while NEEDS_HUMAN",
            http.post("/commands", json=worklist("next", column_1[:1])),
            409,
        )
        show(
            "F3: human clears the device",
            http.post("/device/clear", json={"operator": "demo", "note": "checked the deck"}),
            200,
        )

    print(
        "\nDone. Open the dashboard to see the event log."
        if not failures
        else f"\nUnexpected: {failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
