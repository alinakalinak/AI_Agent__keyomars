"""Safe deterministic planning fallback and final independent validation.

This is not called 'AI planning': it is a transparent rule-based topological
scheduler, used only when the model cannot provide an acceptable proposal.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path
from .validator import load_contract, validate_schedule


class PlanningError(ValueError):
    pass


def build_fallback_schedule(project: Path) -> dict[str, Any]:
    data, config, _ = load_contract(project)
    tasks = data['tasks']
    task_map = {t['id']: t for t in tasks}
    if len(task_map) != len(tasks):
        raise PlanningError('شناسه‌های تکراری در tasks.json وجود دارد.')
    optional = set(config.get('optional_task_ids', []))
    if not optional.issubset(task_map):
        raise PlanningError('کارهای اختیاری ناشناخته در تنظیمات وجود دارد.')
    required = set(task_map) - optional
    # Optional prerequisites of required tasks become mandatory to this plan.
    def closure(tid: str, visiting: set[str]) -> None:
        if tid in visiting:
            raise PlanningError('وابستگی چرخه‌ای در تسک‌های پروژه وجود دارد.')
        if tid not in task_map:
            raise PlanningError(f'وابستگی ناشناخته: {tid}')
        visiting.add(tid)
        for dep in task_map[tid].get('depends_on', []):
            required.add(dep)
            closure(dep, visiting)
        visiting.remove(tid)

    for tid in list(required):
        closure(tid, set())

    result_order: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()
    def visit(tid: str) -> None:
        if tid in visiting:
            raise PlanningError('چرخه وابستگی در پروژه یافت شد.')
        if tid in visited:
            return
        if tid not in task_map:
            raise PlanningError(f'شناسه تسک {tid} در tasks.json وجود ندارد.')
        visiting.add(tid)
        for parent in task_map[tid].get('depends_on', []):
            visit(parent)
        visiting.remove(tid)
        visited.add(tid)
        if tid in required:
            result_order.append(tid)

    for tid in sorted(required):
        visit(tid)

    count = config.get('working_days')
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 31:
        raise PlanningError('تعداد روزهای کاری نامعتبر است.')
    per_day = float(config['hours_per_day'])
    extra_max = float(config.get('max_extra_hours_for_approval', 0))
    if per_day <= 0 or extra_max < 0:
        raise PlanningError('ظرفیت روزانه یا سقف اضافه‌کاری نامعتبر است.')

    days = [{'day': index + 1, 'tasks': []} for index in range(count)]
    day = 0
    used = [0.0] * count
    for tid in result_order:
        t = task_map[tid]
        if str(t.get('status', '')).lower() in ('done', 'completed'):
            continue
        remaining = round(float(t['estimate_hours']), 3)
        while remaining > 0.00001:
            if day >= count:
                # May extend the last day, but never exceed its explicit limit.
                day = count - 1
                room = round(per_day + extra_max - used[day], 3)
            else:
                room = round(per_day - used[day], 3)
            if room <= 0.00001:
                if day >= count - 1:
                    # Regular capacity and permitted overtime have both run out.
                    # Explicit failure is safer than silently creating an infeasible plan.
                    if used[day] >= per_day + extra_max - 0.00001:
                        raise PlanningError('کارهای ضروری از ظرفیت و سقف اضافه‌کاری مجاز بیشترند؛ دامنه یا زمان باید اصلاح شود.')
                day += 1
                continue
            alloc = round(min(remaining, room), 3)
            days[day]['tasks'].append({'id': tid, 'hours': alloc})
            remaining = round(remaining - alloc, 3)
            used[day] = round(used[day] + alloc, 3)
            if day == count - 1 and remaining > 0.00001 and used[day] >= per_day + extra_max - 0.00001:
                raise PlanningError('کارهای ضروری از ظرفیت و سقف اضافه‌کاری مجاز بیشترند؛ دامنه یا زمان باید اصلاح شود.')
    deferred = [
        {'id': tid, 'reason': 'این قابلیت در محدوده نسخه MVP ضروری نیست و به فاز بعدی موکول می‌شود.'}
        for tid in sorted(optional - required)
        if str(task_map[tid].get('status', '')).lower() not in ('done', 'completed')
    ]
    proposal = {'days': days, 'deferred': deferred}
    verdict = validate_schedule(project, **proposal)
    if verdict['verdict'] == 'invalid':
        details = '؛ '.join(x['message'] for x in verdict['issues'] if x['severity'] == 'error')
        raise PlanningError('برنامه جایگزین هم معتبر نشد: ' + details[:400])
    return proposal
