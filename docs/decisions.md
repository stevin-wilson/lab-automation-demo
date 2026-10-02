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
- **Chosen:** one forced tool call returns a worklist. The same `validate()` and a human prompt gate it.
- **Why:** it is the smallest thing that shows the principle that AI proposes and code and a human decide. Responses are recorded so the demo works offline.
- **Revisit when:** the flow has multiple steps. Then use a LangGraph graph with an interrupt-based approval node.

## D7. Scope cuts for the weekend build
- **Chosen:** no containers, file watcher, plate reader, heatmap or lineage UI. See spec §11 for the order they would be added.
- **Why:** the build is capped at about five hours, and every file must stay explainable.
