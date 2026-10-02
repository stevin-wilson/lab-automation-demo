# Decision records

One entry per decision, newest at the bottom. Each states what was chosen, why, and when to revisit it. The design context is in the [spec](specs/2026-09-30-simulated-lab-integration.md).

## D1. The edge agent is an in-process module, not a separate service
- **Chosen:** `Device` is a class inside the FastAPI process.
- **Why:** The goal is to learn and show the control-side patterns. A separate process adds networking and failure modes that cost more build time than they teach.
- **Revisit when:** there is more than one instrument, or the agent must run on the instrument PC.

## D2. Commands execute synchronously
- **Chosen:** `POST /commands` blocks until the run finishes.
- **Why:** The simulated runs are fast, and one request/response keeps the idempotency logic easy to follow.
- **Revisit when:** real runs are long. Then use `202 Accepted` with status polling or events, and make timeouts meaningful (`UNKNOWN_OUTCOME`).

## D3. SQLite for the command ledger and event log
- **Chosen:** a single SQLite file.
- **Why:** zero-ops, offline, and enough for one process.
- **Revisit when:** there is more than one writer. Then use PostgreSQL.

## D4. Hand-written state machine
- **Chosen:** an enum plus a literal transition table.
- **Why:** it is about 30 lines and can be explained completely. Every legal and illegal move is visible in one place.
- **Revisit when:** the number of states or guards grows.

## D5. A command is recorded before it executes, and a failed command is never retried
- **Chosen:** insert `in_progress` first. A resend of any known `command_id` returns the stored record.
- **Why:** if the reply is lost, a retry must never dispense twice. Recovery is a human decision plus a new `command_id`.

## D6. Plain Anthropic SDK for the AI gate
- **Chosen:** one tool call returns a worklist (`tool_choice: auto` plus an instruction to call it, see D9). The same `validate()` and a human prompt gate it.
- **Why:** it is the smallest thing that shows the principle that AI proposes and code and a human decide. Responses are recorded so the demo works offline.
- **Revisit when:** the flow has multiple steps. Then use a LangGraph graph with an interrupt-based approval node.

## D7. Scope cuts for the weekend build
- **Chosen:** no containers, file watcher, plate reader, heatmap or lineage UI. See spec §11 for the order they would be added.
- **Why:** the build is capped at about five hours, and every file must stay explainable.
- **Later:** the mock plate reader, the file watcher and per-well lineage were added on 2026-10-02 (D11). Containers, a heatmap and a lineage UI are still cut.

## D8. Look up the command_id before validating, and reject a reused id with a different worklist
- **Chosen:** `POST /commands` checks the ledger first. Same worklist → duplicate (`200`). Different worklist → `409`. Only then validate, check state and execute.
- **Why:** found while writing the plan. Validating first would reject the resend of a command that filled a well (its own volume now counts against the 300 µL cap), when the right answer is "duplicate". Returning the stored result for a different worklist would silently hide a client bug.
- **Revisit when:** command IDs gain a time-to-live, or the ledger moves to a shared database.

## D9. Do not force the tool call: `tool_choice: auto` plus an instruction
- **Chosen:** `draft_live` sends one tool, `propose_worklist`, with `tool_choice: {"type": "auto"}` and the system-prompt sentence "Always answer by calling the propose_worklist tool." `max_tokens` is 4096 because adaptive thinking is on by default for this model and uses output tokens.
- **Why:** the first live run returned HTTP 400 (`tool_choice: type "tool" and "any" are not supported for this model`), so forced tool use is not available on `claude-sonnet-5-5`. If the model ever answers without calling the tool, `draft_live` raises `RuntimeError` and `main` exits 4 with a message pointing to `--replay`.
- **Revisit when:** structured outputs (`output_config.format`) become preferable to a tool call.

## D10. Releases are version tags that publish a wheel and an sdist to GitHub Releases
- **Chosen:** pushing a `v*` tag runs `.github/workflows/release.yml`. It re-runs CI, checks the tag against the `pyproject.toml` version and that the commit is on `main`, builds with `uv build`, smoke-tests an API served from the built wheel, and attaches the wheel and sdist to a GitHub Release. No container image and no hosted deployment, so D7 still stands.
- **Why:** it is the smallest CD that yields a versioned, tested artifact. It needs no hosting account or secret beyond the workflow's own token, and it does not put an unauthenticated API with fault and clear endpoints on the public internet.
- **Revisit when:** containerizing (spec §11 item 3). Then the release also builds, smoke-tests and pushes an image to GHCR.

## D11. Readouts arrive as files that a polling watcher posts to the API
- **Chosen:** a mock plate reader writes a CSV into an inbox folder (temp file, then an atomic rename). `labdemo.watcher` polls the folder every 2 s, parses each `*.csv`, and posts it to `POST /readouts`. It is a client of the API, like `ai_draft`. The `readout_id` is the SHA-256 of the file's bytes, and the API stores each id once. A resend is a duplicate, and the same id with different readings gets `409` (as in D8). The API snapshots each well's lineage at ingest: its value, its volume and the commands whose completed transfers filled it. Processed and rejected files are moved, never deleted.
- **Why:** this is the smallest version of spec §11 item 4 that keeps the architecture honest. The API stays the only SQLite writer, so D3 still holds, and validation lives in one place. Polling needs no new dependency and is easy to explain. A content hash makes delivery at-least-once and safe: a file is left in place when the API is down and resent later without being stored twice.
- **Revisit when:** files arrive faster than every few seconds, or the reader can push results itself. Then use OS file events (`watchdog`) or a message queue. Also revisit when real vendor exports need parsing; then use one parser per format behind `parse_csv`.
