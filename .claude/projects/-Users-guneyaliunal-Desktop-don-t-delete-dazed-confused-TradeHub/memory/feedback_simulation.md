---
name: simulation_approach
description: User rejected headless simulation harness; keep testing focused on pytest unit/integration tests per CLAUDE.md
type: feedback
---

Do not build "headless simulation" or multi-player stress-test scripts as standalone tools. The user found this approach invalid.

**Why:** Shared SQLAlchemy sessions across concurrent coroutines cause flush conflicts. The design was also disconnected from realistic usage.

**How to apply:** For load/stress testing stay within pytest using the in-memory SQLite fixture. Follow the testing strategy defined in CLAUDE.md: unit tests in `tests/test_order_engine.py` and `tests/test_competition.py`, no external simulation scripts.
