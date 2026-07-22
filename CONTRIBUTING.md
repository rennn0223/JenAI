# Contributing to JenAI

JenAI accepts fixes, tests, documentation, vehicle adapters, and bounded high-level capabilities.
Changes that bypass approval, speed limits, watchdogs, the navigation gateway, or honest failure
reporting will not be accepted.

## Development setup

```bash
git clone https://github.com/rennn0223/JenAI
cd JenAI
uv sync
env -u PYTHONPATH uv run pytest -q
env -u PYTHONPATH uv run ruff check src tests scripts
```

Run the application in a ROS-aware shell:

```bash
source /opt/ros/jazzy/setup.bash
uv run JenAI doctor
uv run JenAI
```

Do not unset ROS `PYTHONPATH` when running the application. Do unset it for tests so ROS system
packages do not shadow the project environment.

## Safety invariants

- LLM code never enters the real-time control or emergency-stop path.
- Vehicle differences remain in the vehicle profile or a thin adapter.
- Every failure is reported honestly; unavailable, blocked, referred, and failed are not success.
- Emergency stop stays approval-free and able to pre-empt queued work.
- Every navigation entry point uses the shared navigation gateway and configured Twin policy.

## Pull request checklist

1. Add or update tests that prove the changed behavior, including failure and cancellation paths.
2. Run inline code review and fix findings.
3. Run lint, the full suite, build, and a clean wheel smoke test.
4. Exercise the affected TUI/ROS2 path from `docs/validation/TEST.md`; use Isaac Sim for motion changes.
5. Update COMMANDS, TEST, TECHNICAL_GUIDE, support matrix, or safety documents when the public
   behavior or boundary changes.
6. UI changes include a before/after screenshot and keyboard-only regression evidence.

Never commit `.env`, credentials, private maps, raw camera data, thesis files, or site-specific
experiment artifacts.
