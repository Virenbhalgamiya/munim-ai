"""Accuracy evaluation against the configured LLM (Groq/Gemini/Claude/mock).

    python -m evals.run_evals                 # all cases, real model from .env
    python -m evals.run_evals --only udhaar   # cases whose id contains "udhaar"
    python -m evals.run_evals --delay 4       # seconds between model turns (free-tier pacing)

Uses the real AI. Runs on a throwaway copy of the database (your real data is never changed), and
Telegram sends are captured instead of delivered, so evaluating never messages anyone.
Writes logs/eval_report.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--delay", type=float, default=float(os.getenv("EVAL_DELAY", "5")))
    ap.add_argument("--min-pass", type=float, default=0.0, help="exit 1 if pass rate is below this (0-1)")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="munim-eval-"))
    os.environ.update({"MUNIM_DB_PATH": str(tmp / "eval.db"), "TELEGRAM_BOT_TOKEN": "eval-capture",
                       "TELEGRAM_CHAT_ID": "eval", "SEED_MERCHANT_PIN": os.getenv("SEED_MERCHANT_PIN") or "482915"})
    from app import config
    from app.services import telegram_service

    captured: list[str] = []

    class _Capture:  # evaluation must never message a real chat
        def send_message(self, text, chat_id=None):
            captured.append(text)
            return {"ok": True, "chat_id": chat_id, "message_id": len(captured)}

    telegram_service.get_telegram_service = lambda: _Capture()
    errors, _ = config.validate()
    if errors:
        print("\n".join(errors))
        return 1
    from app.agent.agent import MunimAgent
    from app.db import database
    from app.db.seed import seed_demo
    from app.observability import setup_logging

    setup_logging()
    seed_demo()
    database.get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    template = tmp / "template.db"
    database.close_conn()
    shutil.copy(config.DB_PATH, template)

    cases = [c for c in json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8")) if args.only in c["id"]]
    print(f"Evaluating {len(cases)} cases on {config.mode_summary()['llm']}\n")
    results, t_all = [], time.time()

    for case in cases:
        database.close_conn()
        shutil.copy(template, config.DB_PATH)          # every case starts from the same data
        from app.db import store
        store.invalidate_cache()
        agent = MunimAgent()
        sid = f"eval-{case['id']}"
        for q in case.get("setup", []):
            agent.ask(q, session_id=sid, lang=case.get("lang", "hi"))
            time.sleep(args.delay)
        t0 = time.time()
        r = agent.ask(case["question"], session_id=sid, lang=case.get("lang", "hi"))
        elapsed = time.time() - t0
        checks = evaluate(case, r)
        ok = all(checks.values())
        results.append({"id": case["id"], "question": case["question"], "pass": ok, "checks": checks,
                        "tools": [{"name": t.name, "input": t.input, "status": t.output.get("status"),
                                   "date": t.output.get("date")} for t in r.tool_calls],
                        "reply": r.reply, "ai_error": r.error, "seconds": round(elapsed, 1)})
        mark = "PASS" if ok else "FAIL"
        failed = [k for k, v in checks.items() if not v]
        print(f"[{mark}] {case['id']:<22} {elapsed:4.1f}s  tools={[t.name for t in r.tool_calls]}"
              f"{'  failed: ' + ', '.join(failed) if failed else ''}{'  (AI unreachable)' if r.error else ''}")
        if not ok:
            print(f"        reply: {r.reply}")
        time.sleep(args.delay)

    passed = sum(r["pass"] for r in results)
    rate = passed / len(results) if results else 0
    by_check: dict[str, list[int]] = {}
    for r in results:
        for k, v in r["checks"].items():
            by_check.setdefault(k, [0, 0])
            by_check[k][0] += int(v)
            by_check[k][1] += 1
    print(f"\nPassed {passed}/{len(results)} ({rate:.0%}) in {time.time() - t_all:.0f}s")
    for k, (a, b) in sorted(by_check.items()):
        print(f"  {k:<16} {a}/{b}")
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    report = {"model": config.mode_summary()["llm"], "passed": passed, "total": len(results), "rate": rate,
              "by_check": by_check, "results": results}
    (config.LOG_DIR / "eval_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {config.LOG_DIR / 'eval_report.json'}")
    return 1 if rate < args.min_pass else 0


def evaluate(case: dict, r) -> dict[str, bool]:
    names = [t.name for t in r.tool_calls]
    checks: dict[str, bool] = {}
    expected = case.get("expect_tools")
    if expected is not None:
        checks["tool_choice"] = (set(expected) <= set(names)) if expected else not any(
            t.output.get("status") in ("sent", "saved") for t in r.tool_calls) and not names
    if "expect_args" in case:
        calls = [t for t in r.tool_calls if t.name in (expected or [])]
        checks["arguments"] = any(all(_eq(t.input.get(k), v) for k, v in case["expect_args"].items()) for t in calls)
    if "expect_output" in case:
        calls = [t for t in r.tool_calls if t.name in (expected or [])]
        checks["tool_output"] = any(all(_eq(t.output.get(k), v) for k, v in case["expect_output"].items()) for t in calls)
    if "expect_status" in case:
        checks["action_status"] = any(t.output.get("status") == case["expect_status"] for t in r.tool_calls)
    if "forbid_status" in case:
        checks["safety"] = not any(t.output.get("status") in case["forbid_status"] for t in r.tool_calls)
    if "reply_contains_any" in case:
        checks["reply_facts"] = any(s.lower() in r.reply.lower() for s in case["reply_contains_any"])
    if case.get("reply_script") == "latin":
        checks["language"] = not any("ऀ" <= ch <= "ॿ" for ch in r.reply)
    if case.get("reply_script") == "devanagari":
        dev = sum("ऀ" <= ch <= "ॿ" for ch in r.reply)
        checks["language"] = dev > len(r.reply) * 0.3
    return checks


def _eq(a, b) -> bool:
    if isinstance(b, str) and isinstance(a, str):
        return a.strip().lower() == b.strip().lower()
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


if __name__ == "__main__":
    raise SystemExit(main())
