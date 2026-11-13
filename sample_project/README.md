# TaskFlow Mini — project sample for Keyomars

A fictional, non-confidential task management web app. We want the **first usable release** by the end of the next five working days.

## Product scope (MVP)
- Backend: Python FastAPI
- Database: SQLite (dev/demo)
- Functions: create task, list tasks, update task status, filter by status
- Quality: tests for endpoints, simple API docs, short deployment instructions

## Known constraints
- Only one developer and ~2 hours of focused development per day.
- Focus on one working flow; do **not** invent extra features.
- Need at least a passing smoke test before marking any task `done`.
- Only the project owner can change task statuses. The agent may propose, not modify.

## Definition of Done
The sample API starts locally, persists basic tasks in a local SQLite database, and its critical routes pass tests. A short README explains how to run it.

The current sample source in `app_stub.py` is **not a completed API**. Use `tasks.json` and `notes.md` to discover the work still needed.
