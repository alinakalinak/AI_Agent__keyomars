"""Launch Keyomars' local RTL web dashboard. Zero third-party pip dependencies.

    python run_web.py

Binds to loopback only. All AI inference stays with local Ollama.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from keyomars_agent.orchestrator import run_controlled_agent
from keyomars_agent.ollama_client import OllamaClient
from keyomars_agent.tools import ProjectTools, MAX_PLAN_CHARS
from keyomars_agent.validator import validate_schedule, load_contract

BASE = Path(__file__).resolve().parent
PROJECT = BASE / "sample_project"  # Fixed sandboxed demo workspace
DEFAULT_TASK = (
    "پروژه نمونه TaskFlow Mini را با ابزارها بررسی کن. از روی README، notes، "
    "tasks و کد، وضعیت واقعی، وابستگی‌ها و سه اولویت مهم را پیدا کن؛ "
    "یک برنامه عملی پنج‌روزه به فارسی بنویس و با save_plan برای ذخیره آن تأیید بگیر."
)
TOOL_TITLES = {
    "list_project_files": "شناسایی فایل‌های پروژه",
    "list_tasks": "بررسی تسک‌ها و وضعیت انجام",
    "read_project_file": "خواندن مستندات و کد",
    "save_plan": "درخواست تأیید و ذخیره برنامه",
    "validate_plan": "کنترل ظرفیت، وابستگی و کارهای جاافتاده",
}


class DashboardState:
    def __init__(self, client_factory: Callable[[str, int], Any] = OllamaClient, project: Path = PROJECT):
        self.client_factory = client_factory
        self.project = project
        self.lock = threading.RLock()
        self.status = "idle"
        self.model = "qwen3:8b"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.logs: list[dict[str, Any]] = []
        self.final = ""
        self.error = ""
        self.plan_preview = ""
        self.plan_path = ""
        self.saved = False
        self.validation: dict[str, Any] | None = None
        self.plan_candidate: dict[str, Any] | None = None
        self.extra_confirmed = False
        self.approval_pending = False
        self.plan_origin = ""
        self.decision: bool | None = None
        self.approval_signal = threading.Event()
        self.thread: threading.Thread | None = None
        self._counter = 0

    def _log(self, message: str, kind: str = "info", details: str = "") -> None:
        self._counter += 1
        self.logs.append({"id": self._counter, "message": message,
                          "kind": kind, "details": details[:280],
                          "time": time.strftime("%H:%M:%S")})
        self.logs = self.logs[-120:]

    def on_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            kind = event.get("kind")
            if kind == "round":
                self._log("کیومرث در حال تحلیل و انتخاب مرحله بعدی است...", "thinking",
                          "مدل محلی در حال پردازش است. ممکن است روی CPU کمی زمان ببرد.")
            elif kind == "tool_call":
                tool = event.get("tool", "")
                args = event.get("args", {})
                detail = args.get("path", "") if isinstance(args, dict) else ""
                if tool == "validate_plan":
                    self.validation = None
                    self.plan_candidate = None
                    if isinstance(args, dict):
                        self.plan_candidate = {"days": args.get("days"), "deferred": args.get("deferred")}
                    detail = "در حال محاسبه مستقل ظرفیت زمانی و وابستگی‌ها..."
                self._log(TOOL_TITLES.get(tool, str(tool)), "running", str(detail))
            elif kind == "tool_result":
                tool = event.get("tool", "")
                result = event.get("result") or {}
                if result.get("error"):
                    if tool == "validate_plan":
                        self.validation = None
                    self._log(f"خطا در {TOOL_TITLES.get(tool, tool)}", "error", str(result["error"]))
                    return
                detail = ""
                if tool == "list_project_files":
                    detail = f"{len(result.get('files', []))} فایل پیدا شد: " + "، ".join(result.get("files", []))
                elif tool == "list_tasks":
                    detail = f"{result.get('count', 0)} تسک از tasks.json خوانده شد."
                elif tool == "read_project_file":
                    detail = f"فایل {result.get('path', 'نامشخص')} با موفقیت خوانده شد."
                elif tool == "validate_plan":
                    self.validation = result
                    label = {"pass":"تأیید شد", "needs_approval":"معتبر، نیازمند تأیید اضافه‌کاری", "invalid":"رد شد؛ نیازمند اصلاح"}.get(result.get("verdict"), "نامشخص")
                    detail = (f"{label} | {result.get('error_count',0)} خطا | "
                              f"{result.get('warning_count',0)} هشدار | "
                              f"{result.get('extra_hours',0)} ساعت اضافه‌کاری")
                    self._log("اعتبارسنجی برنامه — " + label,
                              "error" if result.get("verdict") == "invalid" else "success", detail)
                elif tool == "save_plan":
                    self.saved = bool(result.get("saved", False))
                    if self.saved:
                        self.plan_path = result.get("path", "")
                        detail = "فقط فایل output/plan.md ذخیره شد؛ وضعیت تسک‌ها تغییری نکرد."
                    else:
                        detail = str(result.get("reason", "ذخیره انجام نشد."))
                self._log(TOOL_TITLES.get(tool, str(tool)) +
                          (" — ذخیره انجام نشد" if tool == "save_plan" and not self.saved else " — انجام شد"),
                          "info" if tool == "save_plan" and not self.saved else "success", detail)
            elif kind == "workflow_stage":
                self._log(str(event.get('message', 'مرحله جدید')), 'info')
            elif kind == "workflow_fallback":
                self._log('برنامه جایگزین توسط Python ساخته می‌شود؛ این خروجی مدل نیست', 'info', str(event.get('message', '')))
            elif kind == "workflow_mode":
                self.plan_origin = str(event.get('mode', ''))
                self._log('منبع برنامه: ' + self.plan_origin, 'info')
            elif kind == "workflow_retry":
                self._log("کیومرث هنوز اعتبارسنجی یا تأیید را تکمیل نکرده؛ تلاش دوباره", "thinking",
                          str(event.get("message", "")))
            elif kind == "workflow_incomplete":
                self._log("کار کیومرث ناقص ماند؛ ذخیره مجاز نیست", "error", str(event.get("message", "")))
            elif kind == "final":
                self.final = str(event.get("content", ""))[:24000]
                self._log("پیشنهاد و جمع‌بندی کیومرث آماده شد", "success")

    def request_approval(self, markdown: str, path: str) -> bool:
        with self.lock:
            self.plan_preview = markdown
            self.plan_path = path
            self.approval_pending = True
            self.decision = None
            self.extra_confirmed = False
            self.status = "approval"
            self.approval_signal.clear()
            self._log("برنامه آماده است؛ برای ذخیره، تأیید تو لازم است", "approval")
        received = self.approval_signal.wait(timeout=900)
        with self.lock:
            approved = bool(received and self.decision is True)
            self.approval_pending = False
            self.status = "running"
            self._log("ذخیره برنامه تأیید شد" if approved else "ذخیره برنامه رد شد یا زمان تأیید گذشت",
                      "success" if approved else "info")
        return approved

    def make_decision(self, approve: bool, approve_extra: bool = False) -> bool:
        with self.lock:
            if not self.approval_pending or self.status != "approval":
                return False
            if approve and (not self.validation or self.validation.get("verdict") == "invalid"):
                return False
            if approve and self.validation.get("extra_hours", 0) > 0 and not approve_extra:
                return False
            self.extra_confirmed = bool(approve and approve_extra)
            self.decision = bool(approve)
            self.approval_signal.set()
            return True

    def _worker(self, model: str, task: str, timeout: int) -> None:
        try:
            tools = ProjectTools(self.project, approve=self.request_approval)
            result = run_controlled_agent(self.client_factory(model, timeout), tools, task,
                               printer=lambda _line: None, event_callback=self.on_event)
            with self.lock:
                if not self.final:
                    self.final = result
                self.status = "done"
                self.finished_at = time.time()
                self._log("تحلیل به پایان رسید", "success")
        except Exception as e:
            logging.exception("Keyomars execution failed")
            with self.lock:
                self.error = str(e)[:1000]
                self.status = "error"
                self.finished_at = time.time()
                self._log("اجرای مدل با مشکل مواجه شد", "error", self.error)

    def start(self, model: str, task: str, timeout: int) -> bool:
        with self.lock:
            if self.status in ("running", "approval"):
                return False
            self.model = model
            self.status = "running"
            self.logs = []
            self._counter = 0
            self.final = ""
            self.error = ""
            self.saved = False
            self.validation = None
            self.plan_candidate = None
            self.plan_origin = ""
            self.extra_confirmed = False
            self.plan_preview = ""
            self.plan_path = ""
            self.approval_pending = False
            self.decision = None
            self.started_at = time.time()
            self.finished_at = None
            self._log("کیومرث مأموریت جدید را دریافت کرد", "info",
                      f"مدل {model} • پروژه نمونه TaskFlow Mini")
            self.thread = threading.Thread(target=self._worker, args=(model, task, timeout), daemon=True)
            self.thread.start()
            return True

    def save_final(self, approve_extra: bool = False) -> dict[str, Any]:
        """A separate user click is approval, but NEVER bypasses deterministic validation."""
        with self.lock:
            if self.status != "done" or self.saved or self.approval_pending:
                return {"ok": False, "message": "در وضعیت فعلی امکان ذخیره وجود ندارد."}
            if not self.validation or not self.plan_candidate or self.validation.get("verdict") == "invalid":
                return {"ok": False, "message": "اول یک برنامه معتبر را با validate_plan تهیه کن."}
            if self.validation.get("extra_hours", 0) > 0 and not approve_extra:
                return {"ok": False, "message": "این برنامه اضافه‌کاری دارد؛ تأیید صریح افزایش ظرفیت لازم است."}
            commentary = self.plan_preview or self.final
            if not commentary.strip() or len(commentary) > MAX_PLAN_CHARS:
                return {"ok": False, "message": "متن برنامه خالی یا بزرگ‌تر از حد مجاز است."}
            tools = ProjectTools(self.project, approve=lambda _markdown, _path: True)
            fresh = tools.validate_plan(**self.plan_candidate)
            if fresh["verdict"] == "invalid" or fresh["source_hash"] != self.validation["source_hash"]:
                return {"ok": False, "message": "منابع پروژه تغییر کرده‌اند یا برنامه دیگر معتبر نیست؛ دوباره تحلیل کن."}
            result = tools.save_plan(commentary)
            self.saved = bool(result.get("saved"))
            if self.saved:
                self.plan_path = result["path"]
                self._log("برنامه اعتبارسنجی‌شده با تأیید مستقیم شما ذخیره شد", "success", "output/plan.md")
            return {"ok": self.saved, "message": "برنامه معتبر ذخیره شد" if self.saved else "ذخیره انجام نشد"}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            output = self.project / "output" / "plan.md"
            tasks = []
            try:
                data = json.loads((self.project / "tasks.json").read_text(encoding="utf-8"))
                tasks = data.get("tasks", [])
            except (OSError, ValueError):
                pass
            complete = sum(str(t.get("status", "")).lower() in ("done", "completed") for t in tasks)
            high = sum(str(t.get("priority", "")).lower() == "high" for t in tasks)
            try:
                _, config, _ = load_contract(self.project)
                optional = set(config.get("optional_task_ids", []))
                planned_hours = sum(float(t.get("estimate_hours", 0)) for t in tasks
                                   if str(t.get("status", "")).lower() not in ("done", "completed"))
                mandatory = sum(float(t.get("estimate_hours", 0)) for t in tasks
                                if t.get("id") not in optional and str(t.get("status", "")).lower() not in ("done", "completed"))
                capacity_summary = {"base_capacity_hours": round(float(config["hours_per_day"]) * int(config["working_days"]), 2),
                                    "mandatory_hours": round(mandatory, 2), "all_hours": round(planned_hours, 2)}
            except (OSError, ValueError, TypeError, KeyError):
                capacity_summary = None
            return {
                "capacity_summary": capacity_summary,
                "status": self.status, "model": self.model,
                "logs": list(self.logs), "final": self.final,
                "error": self.error, "plan_preview": self.plan_preview,
                "approval_pending": self.approval_pending, "saved": self.saved,
                "validation": self.validation, "plan_origin": self.plan_origin,
                "output_exists": output.is_file() and self.saved,
                "elapsed": round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0,
                "project": "TaskFlow Mini", "task_count": len(tasks),
                "task_done": complete, "task_high": high,
                "output_path": "sample_project/output/plan.md",
            }


STATE = DashboardState()


class Handler(BaseHTTPRequestHandler):
    server_version = "BrainByteLocal/0.3.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.path.startswith("/api/state"):
            logging.info("HTTP %s", fmt % args)

    def _send(self, data: bytes, mime: str, status: int = 200, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        assets = {
            "/": (BASE / "web" / "index.html", "text/html; charset=utf-8"),
            "/web/styles.css": (BASE / "web" / "styles.css", "text/css; charset=utf-8"),
            "/web/app.js": (BASE / "web" / "app.js", "application/javascript; charset=utf-8"),
            "/web/assets/keyomars.png": (BASE / "web" / "assets" / "keyomars.png", "image/png"),
        }
        if path == "/api/state":
            return self._json(STATE.snapshot())
        if path == "/api/download-plan":
            # Only offer a fixed file in the fixed project workspace.
            target = PROJECT / "output" / "plan.md"
            if not target.is_file():
                return self._json({"error": "هنوز برنامه‌ای ذخیره نشده است."}, 404)
            return self._send(target.read_bytes(), "text/markdown; charset=utf-8", extra={
                "Content-Disposition": "attachment; filename=keyomars-plan.md"
            })
        if path in assets:
            filename, mime = assets[path]
            return self._send(filename.read_bytes(), mime)
        return self._json({"error": "مسیر پیدا نشد."}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        # Custom header ensures ordinary cross-site HTML forms cannot trigger writes.
        if self.headers.get("X-Keyomars-UI") != "1":
            return self._json({"error": "درخواست نامعتبر است."}, 403)
        origin = self.headers.get("Origin", "")
        if origin and origin != "http://127.0.0.1:7860":
            return self._json({"error": "درخواست از دامنه مجاز نیست."}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 8192:
                return self._json({"error": "درخواست بیش از حد بزرگ است."}, 413)
            data = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object")
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "ورودی JSON نامعتبر است."}, 400)
        if path == "/api/start":
            model = str(data.get("model", "qwen3:8b")).strip()
            task = str(data.get("task", DEFAULT_TASK)).strip()
            try:
                timeout = int(data.get("timeout", 600))
            except (ValueError, TypeError):
                return self._json({"error": "مقدار timeout معتبر نیست."}, 400)
            if not model or len(model) > 70 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/" for c in model):
                return self._json({"error": "نام مدل معتبر نیست."}, 400)
            if not task or len(task) > 1500 or timeout < 60 or timeout > 1800:
                return self._json({"error": "دستور یا زمان انتظار نامعتبر است."}, 400)
            ok = STATE.start(model, task, timeout)
            return self._json({"ok": ok, "error": None if ok else "کیومرث هنوز در حال اجراست."}, 200 if ok else 409)
        if path == "/api/approval":
            approved = data.get("approved")
            if not isinstance(approved, bool):
                return self._json({"error": "باید تأیید یا رد را انتخاب کنید."}, 400)
            approve_extra = data.get("approve_extra", False)
            if not isinstance(approve_extra, bool):
                return self._json({"error": "مقدار تأیید اضافه‌کاری نامعتبر است."}, 400)
            ok = STATE.make_decision(approved, approve_extra)
            return self._json({"ok": ok, "error": None if ok else "برای ذخیره باید اضافه‌کاری را صریحاً تأیید کنی یا درخواست را رد کنی."}, 200 if ok else 409)
        if path == "/api/save-final":
            approve_extra = data.get("approve_extra", False)
            if not isinstance(approve_extra, bool):
                return self._json({"error": "مقدار تأیید اضافه‌کاری نامعتبر است."}, 400)
            return self._json(STATE.save_final(approve_extra=approve_extra))
        return self._json({"error": "مسیر پیدا نشد."}, 404)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    host, port = "127.0.0.1", 7860
    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"\nBrainByte Keyomars UI v0.3.2 => http://{host}:{port}")
        print("پنل کیومرث باز می‌شود. برای توقف سرور Ctrl+C را بزن.")
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print("\nسرور بسته شد.")


if __name__ == "__main__":
    main()
