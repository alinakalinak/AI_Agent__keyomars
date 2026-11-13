"""Explicit, bounded tool capabilities for a *demo* project workspace.

Never pass the model a Python/shell execution tool or arbitrary file write access.
All paths are rooted in an explicitly chosen sample project folder.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable
from .validator import validate_schedule, load_contract, render_verified_markdown


READABLE_EXTENSIONS = {".md", ".txt", ".json", ".py"}
MAX_READ_BYTES = 24_000
MAX_PLAN_CHARS = 12_000
MAX_FILES_LISTED = 70


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "List readable files in the sample project. No reading or writing.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read a small text, Python, Markdown, or JSON file inside the sample project. Never execute it.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative path as returned by list_project_files"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Read and summarize tasks from the project's tasks.json; cannot change statuses.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_plan",
            "description": "MANDATORY before save_plan: verify a five-day structured schedule against real tasks.json and project_constraints.json. Partial task-hour allocations may continue on later days; list every required task; explicitly explain omitted optional tasks. Tool detects missed tasks, dependency order, daily overtime, and requires user approval for extra hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "array", "description": "Exactly five ordered days. Each day has day number 1..5 and tasks in execution order. Tasks may be split across days by allocating partial hours.", "items": {
                        "type": "object", "properties": {
                            "day": {"type": "integer"},
                            "tasks": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "hours": {"type": "number"}}, "required": ["id", "hours"]}},
                        }, "required": ["day", "tasks"]
                    }},
                    "deferred": {"type": "array", "description": "Explicit explanations for optional tasks excluded from the 5-day MVP schedule", "items": {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id", "reason"]}},
                },
                "required": ["days", "deferred"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_plan",
            "description": "Only AFTER validate_plan returns pass or needs_approval, request human approval and save the verified schedule to output/plan.md. Never try saving an invalid or unchecked plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "Complete Persian Markdown action plan with evidence and priorities"}
                },
                "required": ["markdown"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolError(ValueError):
    pass


class ProjectTools:
    def __init__(self, root: Path, approve: Callable[[str, str], bool]):
        root = root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ToolError(f"Not a directory: {root}")
        self.root = root
        self.approve = approve
        self._save_requested = False  # one request per run: no approval spam
        self._validation: dict[str, Any] | None = None

    def _resolve_readable(self, path: str) -> Path:
        if not isinstance(path, str) or not path or len(path) > 250:
            raise ToolError("path must be a short non-empty relative string")
        if path.startswith("/") or path.startswith("\\") or ":" in path or "\\" in path:
            raise ToolError("absolute and Windows paths are not allowed")
        parts = Path(path).parts
        if ".." in parts or any(p.startswith(".") for p in parts):
            raise ToolError("dot files and parent traversal are not allowed")
        if parts[0] == "output":
            raise ToolError("agent cannot read output files")
        raw = self.root / Path(path)
        if raw.is_symlink() or any((self.root / Path(*parts[:i])).is_symlink() for i in range(1, len(parts))):
            raise ToolError("symlink paths are not allowed")
        resolved = raw.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ToolError("file must reside in project folder")
        if resolved.suffix.lower() not in READABLE_EXTENSIONS:
            raise ToolError("file type not allowed")
        if resolved.stat().st_size > MAX_READ_BYTES:
            raise ToolError("file exceeds read size limit")
        return resolved

    def list_project_files(self) -> dict[str, Any]:
        names: list[str] = []
        for f in self.root.rglob("*"):
            if len(names) >= MAX_FILES_LISTED:
                break
            try:
                relative = f.relative_to(self.root).as_posix()
                if "output" in f.relative_to(self.root).parts:
                    continue
                if f.is_file() and f.suffix.lower() in READABLE_EXTENSIONS:
                    self._resolve_readable(relative)
                    names.append(relative)
            except (OSError, ToolError, ValueError):
                continue
        return {"files": sorted(names), "truncated": len(names) == MAX_FILES_LISTED}

    def read_project_file(self, path: str) -> dict[str, str]:
        p = self._resolve_readable(path)
        return {"path": str(p.relative_to(self.root).as_posix()), "content": p.read_text(encoding="utf-8")}

    def list_tasks(self) -> dict[str, Any]:
        try:
            file = self._resolve_readable("tasks.json")
            data = json.loads(file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise ToolError(f"tasks.json is missing or invalid: {e}") from e
        items = data.get("tasks") if isinstance(data, dict) else data
        if not isinstance(items, list) or len(items) > 100:
            raise ToolError("tasks.json must contain a tasks array with at most 100 items")
        return {"tasks": items, "count": len(items), "source": "tasks.json", "read_only": True}

    def validate_plan(self, days: Any, deferred: Any) -> dict[str, Any]:
        """Deterministic checks only. No writes or permission grants."""
        result = validate_schedule(self.root, days, deferred)
        self._validation = result
        return result

    @property
    def validation(self) -> dict[str, Any] | None:
        return self._validation

    def save_plan(self, markdown: str) -> dict[str, Any]:
        if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_PLAN_CHARS:
            raise ToolError("markdown must be a nonempty string shorter than 12000 characters")
        if self._validation is None:
            raise ToolError("قبل از ذخیره باید validate_plan را اجرا کنی.")
        if self._validation["verdict"] == "invalid":
            raise ToolError("برنامه خطای قطعی دارد؛ تسک‌ها، ساعت‌ها یا وابستگی‌ها را اصلاح و دوباره اعتبارسنجی کن.")
        if load_contract(self.root)[2] != self._validation["source_hash"]:
            raise ToolError("فایل‌های منبع پس از اعتبارسنجی تغییر کردند؛ دوباره validate_plan را اجرا کن.")
        destination = self.root / "output" / "plan.md"
        if destination.parent.is_symlink() or destination.is_symlink():
            raise ToolError("مسیر خروجی symlink است و ذخیره ایمن نیست.")
        if self._save_requested:
            return {"saved": False, "reason": "save_plan already requested once this run; do not ask again"}
        self._save_requested = True
        document = render_verified_markdown(self._validation, self.root, markdown)
        if not self.approve(document, str(destination)):
            return {"saved": False, "reason": "User did not approve the proposed write"}
        # Recheck after the user has spent time reading the approval dialog.
        if load_contract(self.root)[2] != self._validation["source_hash"]:
            raise ToolError("منابع پروژه طی تأیید تغییر کردند؛ دوباره اعتبارسنجی کن.")
        if destination.parent.is_symlink() or destination.is_symlink():
            raise ToolError("مسیر خروجی تغییر کرده و ذخیره امن نیست.")
        destination.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, prefix=".plan-", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(document)
        try:
            os.replace(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)
        return {"saved": True, "path": str(destination), "note": "Only output/plan.md was written; tasks and source files were untouched"}

    def execute(self, name: str, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be a JSON object")
        allowed: dict[str, set[str]] = {
            "list_project_files": set(), "read_project_file": {"path"},
            "list_tasks": set(), "validate_plan": {"days", "deferred"}, "save_plan": {"markdown"},
        }
        if name not in allowed:
            raise ToolError(f"Unknown tool: {name}")
        if set(arguments) - allowed[name]:
            raise ToolError("Unexpected tool arguments")
        if name == "list_project_files":
            return self.list_project_files()
        if name == "read_project_file":
            return self.read_project_file(arguments.get("path"))
        if name == "list_tasks":
            return self.list_tasks()
        if name == "validate_plan":
            return self.validate_plan(arguments.get("days"), arguments.get("deferred"))
        return self.save_plan(arguments.get("markdown"))
