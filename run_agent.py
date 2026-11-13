"""Legacy CMD interface; recommended GUI: python run_web.py"""
from __future__ import annotations
import argparse
from pathlib import Path
from keyomars_agent.orchestrator import run_controlled_agent
from keyomars_agent.ollama_client import OllamaClient
from keyomars_agent.tools import ProjectTools


def approval(markdown: str, path: str) -> bool:
    print("\nکیومرث درخواست ذخیره برنامه دارد.\n", path)
    print("\nپیش‌نمایش:\n", markdown[:2000])
    try:
        return input("برای ذخیره YES را بنویس: ").strip() == "YES"
    except EOFError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Keyomars v0.3.2 - safe local project assistant")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--project", type=Path, default=Path(__file__).parent / "sample_project")
    parser.add_argument("--task", default="این پروژه نمونه را بررسی کن، تسک‌های عقب‌مانده را اولویت‌بندی کن و یک برنامه ۵ روزه پیشنهاد و برای ذخیره درخواست تأیید کن.")
    args = parser.parse_args()
    tools = ProjectTools(args.project, approval)
    print(f"BrainByte Keyomars v0.3.2 | مدل: {args.model} | پروژه: {tools.root}")
    try:
        run_controlled_agent(OllamaClient(args.model, timeout=args.timeout), tools, args.task)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
