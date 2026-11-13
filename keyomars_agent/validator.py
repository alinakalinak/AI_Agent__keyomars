"""Deterministic, read-only schedule checks for the BrainByte demonstration.

The language model suggests a schedule. This module, not the model, checks:
- task and day identity, complete coverage or explained optional deferrals
- hours per task and per day (including explicit approval for capacity changes)
- prerequisites before a task's first work allocation (same-day order supported)
- cyclic or missing task dependency metadata

All calculations are sourced from project files and are *not* AI judgments.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

EPS = 1e-5


def load_contract(project: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    for f in (project / "tasks.json", project / "project_constraints.json"):
        if f.is_symlink():
            raise ValueError("فایل‌های منبع نمی‌توانند symlink باشند.")
    tasks_raw = (project / "tasks.json").read_bytes()
    constraints_raw = (project / "project_constraints.json").read_bytes()
    if len(tasks_raw) > 24_000 or len(constraints_raw) > 8_000:
        raise ValueError("فایل تسک‌ها یا محدودیت‌ها بیش از حد بزرگ است.")
    tasks = json.loads(tasks_raw)
    constraints = json.loads(constraints_raw)
    if not isinstance(tasks, dict) or not isinstance(tasks.get("tasks"), list):
        raise ValueError("ساختار tasks.json معتبر نیست.")
    if not isinstance(constraints, dict):
        raise ValueError("ساختار project_constraints.json معتبر نیست.")
    checksum = hashlib.sha256(tasks_raw + b"\x00" + constraints_raw).hexdigest()
    return tasks, constraints, checksum


def _number(value: Any, label: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label} باید عدد معتبر و بزرگ‌تر یا مساوی {minimum} باشد.")
    return float(value)


def validate_schedule(project: Path, days: Any, deferred: Any) -> dict[str, Any]:
    data, config, checksum = load_contract(project)
    working_days = config.get("working_days")
    if isinstance(working_days, bool) or not isinstance(working_days, int) or not 1 <= working_days <= 31:
        raise ValueError("working_days باید عدد صحیح بین ۱ تا ۳۱ باشد.")
    capacity_per_day = _number(config.get("hours_per_day"), "hours_per_day", 0.1)
    max_extra = _number(config.get("max_extra_hours_for_approval", 2), "max_extra_hours_for_approval")
    optional = config.get("optional_task_ids", [])
    if not isinstance(optional, list) or any(not isinstance(v, str) for v in optional):
        raise ValueError("optional_task_ids باید لیست شناسه باشد.")
    if not isinstance(days, list) or len(days) != working_days:
        raise ValueError(f"برنامه باید دقیقاً {working_days} روز داشته باشد.")
    if not isinstance(deferred, list) or len(deferred) > 100:
        raise ValueError("deferred باید آرایه توضیحات کارهای موکول‌شده باشد.")
    raw_tasks = data["tasks"]
    if len(raw_tasks) > 100:
        raise ValueError("بیش از ۱۰۰ تسک پشتیبانی نمی‌شود.")
    task_map = {}
    for t in raw_tasks:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not t["id"]:
            raise ValueError("شناسه تسک نامعتبر است.")
        if t["id"] in task_map:
            raise ValueError("شناسه تسک تکراری در tasks.json است.")
        _number(t.get("estimate_hours"), f"estimate_hours: {t['id']}", 0.01)
        if not isinstance(t.get("depends_on", []), list):
            raise ValueError(f"وابستگی‌های {t['id']} نامعتبرند.")
        task_map[t["id"]] = t
    issues: list[dict[str, str]] = []
    def add(code: str, severity: str, message: str) -> None:
        issues.append({"code": code, "severity": severity, "message": message})

    if len(set(optional)) != len(optional):
        add("CONFIG_OPTIONAL_DUPLICATE", "error", "شناسه‌های کار اختیاری در تنظیمات تکرار شده‌اند.")
    if set(optional) - set(task_map):
        add("CONFIG_UNKNOWN_OPTIONAL", "error", "شناسه کار اختیاری ناشناخته است.")
    # Validate the source dependency graph before a schedule can be trusted.
    for tid, t in task_map.items():
        for dep in t.get("depends_on", []):
            if dep not in task_map:
                add("UNKNOWN_DEPENDENCY", "error", f"تسک {tid} به شناسه ناشناخته {dep} وابسته است.")
    visited, active = set(), set()
    def walk(tid: str) -> None:
        if tid in active:
            add("DEPENDENCY_CYCLE", "error", f"چرخه وابستگی از {tid} عبور می‌کند.")
            return
        if tid in visited:
            return
        active.add(tid)
        for dep in task_map[tid].get("depends_on", []):
            if dep in task_map:
                walk(dep)
        active.remove(tid)
        visited.add(tid)
    for tid in task_map:
        walk(tid)

    deferrals: dict[str, str] = {}
    for obj in deferred:
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not isinstance(obj.get("reason"), str):
            raise ValueError("هر deferred باید id و reason متنی داشته باشد.")
        tid = obj["id"].strip()
        reason = obj["reason"].strip()
        if tid in deferrals:
            add("DUPLICATE_DEFERRED", "error", f"{tid} بیش از یک بار موکول شده است.")
        if tid not in task_map:
            add("UNKNOWN_DEFERRED", "error", f"تسک موکول‌شده {tid} وجود ندارد.")
        if tid not in optional:
            add("REQUIRED_DEFERRED", "error", f"تسک ضروری {tid} بدون تغییر رسمی دامنه پروژه قابل حذف نیست.")
        if len(reason) < 12:
            add("MISSING_REASON", "error", f"دلیل موکول‌کردن {tid} خیلی کوتاه یا خالی است.")
        deferrals[tid] = reason[:600]

    daily: list[dict[str, Any]] = []
    planned: dict[str, float] = {k: 0.0 for k in task_map}
    seen_days = set()
    for i, block in enumerate(days):
        if not isinstance(block, dict) or not isinstance(block.get("day"), int) or isinstance(block.get("day"), bool):
            raise ValueError("هر روز باید فیلد day عدد صحیح داشته باشد.")
        daynum = block["day"]
        if not 1 <= daynum <= working_days or daynum in seen_days:
            add("INVALID_DAY", "error", f"روز {daynum} تکراری یا خارج از بازه است.")
        if daynum != i + 1:
            add("DAY_ORDER", "error", f"ترتیب روزها باید از ۱ تا {working_days} صعودی باشد.")
        seen_days.add(daynum)
        entries = block.get("tasks")
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError(f"tasks روز {daynum} باید آرایه باشد.")
        total = 0.0
        normalized = []
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("هر تخصیص باید id داشته باشد.")
            tid = item["id"].strip()
            hours = _number(item.get("hours"), f"hours: {tid}", 0.01)
            if hours > 1000:
                raise ValueError("ساعت تخصیص بیش از حد بزرگ است.")
            total += hours
            normalized.append({"id": tid, "hours": round(hours, 3)})
            if tid not in task_map:
                add("UNKNOWN_TASK", "error", f"تسک {tid} در روز {daynum} وجود ندارد.")
                continue
            if tid in deferrals:
                add("DEFERRED_AND_SCHEDULED", "error", f"{tid} هم زمان‌بندی و هم موکول شده است.")
            t = task_map[tid]
            if str(t.get("status", "")).lower() in ("done", "completed"):
                add("ALREADY_COMPLETED", "error", f"{tid} قبلاً تکمیل شده و نباید ساعت تازه مصرف کند.")
            for dep in t.get("depends_on", []):
                if dep in task_map and str(task_map[dep].get("status", "")).lower() not in ("done", "completed"):
                    needed = _number(task_map[dep]["estimate_hours"], "estimate_hours", 0.01)
                    if planned[dep] + EPS < needed:
                        add("DEPENDENCY_ORDER", "error", f"در روز {daynum}، {tid} پیش از تکمیل پیش‌نیاز {dep} شروع شده است.")
            planned[tid] += hours
        daily.append({"day": daynum, "tasks": normalized, "hours": round(total, 3),
                      "base_capacity": round(capacity_per_day, 3),
                      "extra_required": round(max(0.0, total-capacity_per_day), 3)})
    if seen_days != set(range(1, working_days + 1)):
        add("MISSING_DAY", "error", "یک یا چند روز در برنامه غایب است.")
    for tid, t in task_map.items():
        if str(t.get("status", "")).lower() in ("done", "completed"):
            continue
        need = _number(t["estimate_hours"], "estimate_hours", 0.01)
        got = planned[tid]
        if tid in deferrals:
            continue
        if got + EPS < need:
            add("TASK_MISSING" if got < EPS else "TASK_UNDERALLOCATED", "error",
                f"{tid}: از {need:g} ساعت موردنیاز، فقط {got:g} ساعت برنامه‌ریزی شده است؛ " +
                ("تسک اختیاری را با دلیل موکول کن." if tid in optional else "این تسک ضروری است."))
        elif got > need + EPS:
            add("TASK_OVERALLOCATED", "error", f"{tid}: تخصیص {got:g} ساعت از تخمین {need:g} ساعت بیشتر است.")

    all_hours = sum(float(t.get("estimate_hours", 0)) for t in task_map.values()
                    if str(t.get("status", "")).lower() not in ("done", "completed"))
    mandatory_hours = sum(float(t.get("estimate_hours", 0)) for tid, t in task_map.items()
                          if tid not in optional and str(t.get("status", "")).lower() not in ("done", "completed"))
    extra = sum(row["extra_required"] for row in daily)
    planned_hours = sum(row["hours"] for row in daily)
    if extra > max_extra + EPS:
        add("OVERTIME_TOO_HIGH", "error", f"اضافه‌کاری {extra:g} ساعت از سقف قابل‌تأیید {max_extra:g} ساعت بیشتر است.")
    elif extra > EPS:
        add("EXTRA_CAPACITY_APPROVAL", "warning", f"برنامه به {extra:g} ساعت اضافه‌کاری نیاز دارد؛ بدون اجازه صریح علیرضا تأیید نیست.")
    has_errors = any(issue["severity"] == "error" for issue in issues)
    verdict = "invalid" if has_errors else ("needs_approval" if extra > EPS else "pass")
    return {
        "verdict": verdict,
        "valid": not has_errors,
        "can_save_with_approval": not has_errors,
        "source_hash": checksum,
        "work_days": working_days,
        "hours_per_day": capacity_per_day,
        "base_capacity_hours": round(working_days * capacity_per_day, 3),
        "mandatory_hours": round(mandatory_hours, 3),
        "total_hours_including_optional": round(all_hours, 3),
        "scheduled_hours": round(planned_hours, 3),
        "extra_hours": round(extra, 3),
        "deferred": [{"id": tid, "reason": reason} for tid, reason in deferrals.items()],
        "daily": daily,
        "issues": issues[:100],
        "error_count": sum(x["severity"] == "error" for x in issues),
        "warning_count": sum(x["severity"] == "warning" for x in issues),
    }


def render_verified_markdown(result: dict[str, Any], project: Path, commentary: str = "") -> str:
    """Canonical schedule, not a best-effort extraction of model-written prose."""
    title = "# برنامه اعتبارسنجی‌شده کیومرث — TaskFlow Mini"
    label = "معتبر با نیاز به تأیید اضافه‌کاری" if result["verdict"] == "needs_approval" else "معتبر در ظرفیت اصلی"
    lines = [title, "", f"**نتیجه اعتبارسنجی:** {label}",
             f"**ظرفیت عادی:** {result['base_capacity_hours']:g} ساعت در {result['work_days']} روز", 
             f"**کار ضروری:** {result['mandatory_hours']:g} ساعت", 
             f"**اضافه‌کاری پیشنهادی:** {result['extra_hours']:g} ساعت (نیازمند تأیید انسانی)", "",
             "## برنامه روزانه بر اساس tasks.json", ""]
    for day in result["daily"]:
        lines.append(f"### روز {day['day']} — {day['hours']:g} ساعت")
        for a in day["tasks"]:
            lines.append(f"- `{a['id']}` — {a['hours']:g} ساعت")
        if day["extra_required"] > EPS:
            lines.append(f"- ⚠️ اضافه‌کاری این روز: {day['extra_required']:g} ساعت")
        lines.append("")
    lines.append("## کارهای موکول‌شده")
    if result["deferred"]:
        for x in result["deferred"]:
            lines.append(f"- `{x['id']}`: {x['reason']}")
    else:
        lines.append("- هیچ موردی")
    if commentary.strip():
        lines.extend(["", "## توضیح و ریسک‌های پیشنهادی مدل (ارزیابی نشده)", "", commentary.strip()[:3500]])
    lines.extend(["", "---", "این گزارش برنامه پیشنهادی است؛ انجام واقعی تسک‌ها یا تکمیل پروژه را تأیید نمی‌کند."])
    return "\n".join(lines)
