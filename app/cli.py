"""Terminal harness: see every tool call + the final reply (real AI, real data), no browser needed.

  python -m app.cli                    interactive chat (type Hinglish; /reset, /quit)
  python -m app.cli --demo             run the scripted demo conversation
  python -m app.cli --say "Aaj ka hisaab kaisa raha?"
  python -m app.cli --check-llm        check the AI key + a real tool call
  python -m app.cli --check-telegram   send a real test message to your Telegram chat
  python -m app.cli --check-otp        send a real OTP SMS to your login phone and verify it
  add --merchant <id> to act as another merchant (local admin access)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app import config

DEMO_SCRIPT = [
    "Aaj ka hisaab kaisa raha?",
    "Mangalvaar ko dhanda kaisa gaya?",
    "Suresh ka kitna udhaar baaki hai?",
    "Kaun se purane customer aana band ho gaye?",
    "Kya cheez nahi bik rahi hai?",
    "Suresh ko payment ka reminder bhej do",
    "Haan, bhej do",
]

_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")
DIM, CYAN, YELLOW, GREEN, RED, RESET = (
    ("\033[2m", "\033[36m", "\033[33m", "\033[32m", "\033[31m", "\033[0m") if _COLOR else ("",) * 6
)
MERCHANT = config.DEFAULT_MERCHANT_ID


def _short(obj, limit: int = 600) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + " …"


def trace(kind: str, payload) -> None:
    if kind == "tool_call":
        print(f"  {YELLOW}→ tool_call{RESET} {payload['name']}({_short(payload['input'])})")
    elif kind == "tool_result":
        print(f"  {DIM}← result   {_short(payload['output'])}{RESET}")
    elif kind == "guard":
        print(f"  {RED}! {payload}{RESET}")


def run_turn(agent, text: str, session_id: str):
    result = agent.ask(text, session_id=session_id, merchant_id=MERCHANT, on_event=trace)
    print(f"{GREEN}Munim:{RESET} {result.reply}\n")
    return result


def banner() -> None:
    print(f"{CYAN}Munim AI ({MERCHANT}) — {config.mode_summary()}{RESET}\n")


def interactive(agent) -> None:
    banner()
    print("Type a question in Hinglish/Hindi/Marathi. Commands: /reset  /quit\n")
    sid = "cli"
    while True:
        try:
            text = input(f"{CYAN}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return
        if text == "/reset":
            agent.reset(sid, MERCHANT)
            print("(session reset)\n")
            continue
        run_turn(agent, text, sid)


def demo(agent) -> None:
    banner()
    agent.reset("demo", MERCHANT)  # start the scripted demo from a clean conversation
    for q in DEMO_SCRIPT:
        print(f"{CYAN}You:{RESET} {q}")
        run_turn(agent, q, "demo")


def check_llm() -> None:
    from app.agent.agent import MunimAgent
    from app.services.llm_service import get_llm_service

    llm = get_llm_service()
    print(f"Provider: {config.LLM_PROVIDER} / model: {config.LLM_MODEL}")
    r = llm.chat("Reply in one short Hinglish sentence.", [{"role": "user", "content": "Namaste!"}], [])
    print("  plain reply OK:", r.text)
    agent = MunimAgent(llm=llm)
    agent.reset("check", MERCHANT)
    res = agent.ask("Suresh ka kitna udhaar baaki hai?", session_id="check", merchant_id=MERCHANT)
    print("  tool use:", [(t.name, t.input) for t in res.tool_calls])
    print("  agent reply:", res.reply)


def check_telegram() -> None:
    from app.services.telegram_service import TelegramService

    print("Telegram:", TelegramService().send_message("✅ Munim AI test message — Telegram connected."))


def check_otp() -> None:
    from app.services.otp_service import get_otp_service

    phone = config.SEED_MERCHANT_PHONE
    svc = get_otp_service()
    vid = svc.send(phone)
    print(f"Real OTP SMS sent to +91 {phone} (verificationId {vid}).")
    code = input("Enter the OTP you received: ").strip()
    svc.verify(phone, vid, code)
    print("OTP verified with Message Central. Login by OTP will work.")


def main() -> int:
    global MERCHANT
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="Munim AI terminal harness")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--say")
    ap.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID, help="merchant id (local admin access)")
    ap.add_argument("--check-llm", action="store_true")
    ap.add_argument("--check-telegram", action="store_true")
    ap.add_argument("--check-otp", action="store_true")
    args = ap.parse_args()
    MERCHANT = args.merchant

    if args.check_telegram:
        check_telegram()
        return 0
    if args.check_otp:
        check_otp()
        return 0
    errors, warnings = config.validate()
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1
    for w in warnings:
        print(f"WARNING: {w}")

    from app.db.seed import bootstrap
    from app.observability import setup_logging
    setup_logging()
    bootstrap()

    if args.check_llm:
        check_llm()
        return 0
    from app.agent.agent import MunimAgent
    agent = MunimAgent()
    if args.demo:
        demo(agent)
    elif args.say:
        banner()
        print(f"{CYAN}You:{RESET} {args.say}")
        run_turn(agent, args.say, "say")
    else:
        interactive(agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
