# lab-automation-demo

A small **simulated** lab-automation integration: an orchestration API that validates worklists, enforces explicit device states and idempotent commands, and drives a **simulated** liquid handler. It also has a gated path where an LLM drafts a worklist and a human approves it.

> This is a learning project. It is not a real instrument integration. The liquid handler is a simulator, all data is **synthetic**, and replayed LLM output is labeled **recorded response**.

**Status:** spec and plan stage. The implementation is built test-first, on a feature branch, from the spec below.

## How this repo was built (spec-driven)

1. **Spec**: [docs/specs/2026-09-30-simulated-lab-integration.md](docs/specs/2026-09-30-simulated-lab-integration.md) defines the goals, the acceptance scenarios (A1, V1, F1, F3, AI1) and the architecture.
2. **Decisions**: [docs/decisions.md](docs/decisions.md) records what was chosen, why, and when to revisit it.
3. **Plan**: `docs/plans/` holds the test-first implementation plan.
4. **Failing test, then code**: each scenario has a test marked `@pytest.mark.spec("<ID>")`. A traceability test fails CI if a scenario in the spec has no test.
5. **CI**: every push runs pre-commit (ruff, ty) and pytest from a clean environment.

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python (3.12) for you.
- Optional: an `ANTHROPIC_API_KEY`, only to re-record the AI step.

## Development

```
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check
uv run ty check
```

## Running the demo

Start the API (an empty `labdemo.db` is created on first run):

```
uv run uvicorn labdemo.api:create_app --factory
```

Open <http://127.0.0.1:8000/docs> for the interactive API.
