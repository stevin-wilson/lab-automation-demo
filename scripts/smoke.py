"""End-to-end smoke check: a real API process, driven by demo.py and the ai_draft replays.

Runs offline against the simulator with a fresh temporary database. Exits 0 when every step
behaved as expected. CI runs it against the source tree; the release job runs it against the
built wheel with --server-cmd.

    uv run python scripts/smoke.py
    uv run python scripts/smoke.py --server-cmd "uv run --isolated --no-project --with dist/X.whl \
        uvicorn labdemo.api:create_app --factory --port {port}"
"""

import argparse
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER = [sys.executable, "-m", "uvicorn", "labdemo.api:create_app", "--factory", "--port"]

# (name, client command, stdin, expected exit code)
STEPS = [
    ("demo.py: A1, V1, F1, F3", [sys.executable, "demo.py"], "", 0),
    (
        "AI1 replay overdose-250ul: rejected, approval not offered",
        [sys.executable, "-m", "labdemo.ai_draft", "--replay", "overdose-250ul"],
        "",
        1,
    ),
    (
        "AI1 replay column-1-50ul: approved and accepted",
        [sys.executable, "-m", "labdemo.ai_draft", "--replay", "column-1-50ul"],
        "y\n",
        0,
    ),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_up(url: str, server: subprocess.Popen, timeout_s: float = 30) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        try:
            if httpx.get(f"{url}/device", timeout=2).is_success:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def stop(server: subprocess.Popen) -> None:
    """Stop the server and its children: `uv run` does not take uvicorn down with it on Windows."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)], capture_output=True)
    else:
        os.killpg(server.pid, signal.SIGTERM)
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-cmd", help="command that starts the API; '{port}' is substituted")
    args = parser.parse_args()

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = os.environ | {"LABDEMO_DB": str(Path(tmp) / "smoke.db"), "LABDEMO_API_URL": url}
        log_path = Path(tmp) / "server.log"
        if args.server_cmd:
            command = shlex.split(args.server_cmd.format(port=port), posix=os.name != "nt")
        else:
            command = [*DEFAULT_SERVER, str(port)]
        print(f"Starting API: {' '.join(command)}", flush=True)
        with log_path.open("w") as log:
            posix = os.name != "nt"
            server = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=posix
            )
        results: list[tuple[str, bool, int, int]] = []
        try:
            if not wait_until_up(url, server):
                print(f"FAIL: the API did not come up at {url}. Server output:")
                print(log_path.read_text(errors="replace"))
                return 1
            for name, client, stdin, expected in STEPS:
                print(f"\n##### {name}", flush=True)
                code = subprocess.run(client, cwd=ROOT, env=env, input=stdin, text=True).returncode
                results.append((name, code == expected, code, expected))
        finally:
            stop(server)

    print("\n##### Smoke summary")
    for name, ok, code, expected in results:
        print(f"{'PASS' if ok else 'FAIL'}: {name} (exit {code}, expected {expected})")
    return 0 if all(ok for _, ok, _, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
