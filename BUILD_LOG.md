# Build log

Honest notes on how this repo was built with AI assistance: what was generated, what was changed, what broke. Newest at the bottom.

## Template

    ## YYYY-MM-DD HH:MM - Task N: <name>
    Decision / change:
    Why:
    What the AI generated vs. what I changed:
    What broke and how I found it:
    What I learned (one sentence):

## 2026-10-01 22:30 - Task 1: Models and validation (V1)

Decision / change:
Implemented models.py and validation.py with comprehensive worklist validation according to the spec.

Why:
Core foundation for the lab automation system - all requests flow through this validation layer before any device interaction occurs.

What the AI generated vs. what I changed:
The AI generated the exact code from the brief without changes. All code was used as-is, following the spec verbatim.

What broke and how I found it:
RED: Initial pytest run showed `ModuleNotFoundError: No module named 'labdemo.models'` - expected since the module didn't exist yet. GREEN: After implementing both models.py and validation.py, all 15 tests passed immediately. No breaking issues during pre-commit checks.

What I learned (one sentence):
Pydantic's permissive models paired with a centralized validation function provide clean separation between request parsing and business rule enforcement.
