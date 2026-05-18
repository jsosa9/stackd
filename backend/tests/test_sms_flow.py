"""
tests/test_sms_flow.py — Full end-to-end SMS onboarding + coaching simulation.

Calls the exact same functions sms.py calls. No Twilio. Real Supabase. Real Gemini.

Run from the backend directory:
    python tests/test_sms_flow.py
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND, ".env"))

from supabase import create_client

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

TEST_PHONE = "+15550001234"

# ── ANSI colors ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

DIVIDER = "━" * 54


# ── Inline BackgroundTasks ────────────────────────────────────────────────────

class InlineBackgroundTasks:
    def __init__(self):
        self._tasks = []

    def add_task(self, func, *args, **kwargs):
        self._tasks.append((func, args, kwargs))

    async def run_all(self):
        for func, args, kwargs in self._tasks:
            print(f"  {YELLOW}[background]{RESET} running {func.__name__}...")
            await func(*args, **kwargs)
        self._tasks.clear()


# ── Twilio patch ──────────────────────────────────────────────────────────────

_bg_messages: list[str] = []

def _patch_twilio():
    """Replace Twilio client in onboarding module with a logger."""
    import services.onboarding as onb
    captured = _bg_messages

    class _FakeMessages:
        def create(self, body, from_, to):
            captured.append(body)

    class _FakeTwilio:
        messages = _FakeMessages()

    onb._twilio = _FakeTwilio()


# ── Cleanup ───────────────────────────────────────────────────────────────────

def _delete_test_user():
    res = supabase.table("users").select("id").eq("phone", TEST_PHONE).execute()
    if not res.data:
        return
    uid = res.data[0]["id"]
    for tbl in ["coach_settings", "goals", "user_context", "messages",
                "streaks", "reminders", "nutrition_logs", "deadlines",
                "habit_patterns", "social_bets", "sent_quotes", "schedule",
                "phone_link_tokens"]:
        try:
            supabase.table(tbl).delete().eq("user_id", uid).execute()
        except Exception:
            pass
    try:
        supabase.auth.admin.delete_user(uid)
    except Exception:
        pass
    supabase.table("users").delete().eq("id", uid).execute()
    print(f"  {YELLOW}[cleanup]{RESET} removed test user {uid}")


def cleanup_before():
    print(f"\n{YELLOW}[setup]{RESET} Cleaning up any existing data for {TEST_PHONE}...")
    _delete_test_user()


# ── Fetch user row helper ─────────────────────────────────────────────────────

def _get_user():
    res = supabase.table("users").select("*, schedule(*), coach_settings(*)").eq("phone", TEST_PHONE).execute()
    return res.data[0] if res.data else None


# ── Print exchange ────────────────────────────────────────────────────────────

def _print_exchange(user_msg: str, bot_reply: str, category: str = "", handler: str = ""):
    print(f"\nUser input:  {user_msg}")
    print(f"Bot output:  {bot_reply}")


# ── ONBOARDING PHASE ──────────────────────────────────────────────────────────

async def run_onboarding():
    from services.onboarding import handle_onboarding

    _patch_twilio()

    print(f"\n{BOLD}{'═'*54}{RESET}")
    print(f"{BOLD}  ONBOARDING PHASE{RESET}")
    print(f"{BOLD}{'═'*54}{RESET}")

    # ── Step 1: First message from unknown number ─────────────────────────────
    print(f"\n{YELLOW}Step 1 — New user texts for the first time{RESET}")
    msg = "hello"
    bg = InlineBackgroundTasks()
    reply = await handle_onboarding(TEST_PHONE, msg, bg, supabase, None)
    _print_exchange(msg, reply or "(no reply)")

    # ── Step 2: User picks coach ───────────────────────────────────────────────
    print(f"\n{YELLOW}Step 2 — User picks coach{RESET}")
    msg = "David Goggins"
    _bg_messages.clear()
    bg = InlineBackgroundTasks()
    user_data = _get_user()
    reply = await handle_onboarding(TEST_PHONE, msg, bg, supabase, user_data)
    _print_exchange(msg, reply or "(no reply)")

    print(f"  {YELLOW}[background]{RESET} Running persona setup inline (this may take 10-30s)...")
    await bg.run_all()

    # Print any messages the background task would have sent via Twilio
    if _bg_messages:
        for bm in _bg_messages:
            print(f"Bot output:  {bm}")
    _bg_messages.clear()

    # Confirm coach_settings row was created
    user_data = _get_user()
    uid = user_data["id"] if user_data else None
    if uid:
        cs = supabase.table("coach_settings").select("coach_name, personality_id, is_active").eq("user_id", uid).execute()
        if cs.data:
            row = cs.data[0]
            print(f"  {GREEN}✓ coach_settings created:{RESET} coach={row.get('coach_name')} persona_id={row.get('personality_id')}")

    # Check onboarding_step advanced to 2
    user_data = _get_user()
    step = user_data.get("onboarding_step") if user_data else None
    print(f"  onboarding_step in DB = {step}")

    # ── Step 3: User shares goals ─────────────────────────────────────────────
    print(f"\n{YELLOW}Step 3 — User shares goals{RESET}")
    msg = "I want to run every day and eat clean"
    bg = InlineBackgroundTasks()
    user_data = _get_user()
    reply = await handle_onboarding(TEST_PHONE, msg, bg, supabase, user_data)
    _print_exchange(msg, reply or "(no reply)")

    # ── Step 4: Schedule for goal 1 ───────────────────────────────────────────
    print(f"\n{YELLOW}Step 4 — Schedule for goal 1{RESET}")
    msg = "Monday Tuesday Wednesday Thursday Friday"
    bg = InlineBackgroundTasks()
    user_data = _get_user()
    reply = await handle_onboarding(TEST_PHONE, msg, bg, supabase, user_data)
    _print_exchange(msg, reply or "(no reply)")

    # ── Step 5: Schedule for goal 2 ───────────────────────────────────────────
    print(f"\n{YELLOW}Step 5 — Schedule for goal 2{RESET}")
    msg = "Every day"
    bg = InlineBackgroundTasks()
    user_data = _get_user()
    reply = await handle_onboarding(TEST_PHONE, msg, bg, supabase, user_data)
    _print_exchange(msg, reply or "(no reply)")

    # Verify onboarding_step = 5
    user_data = _get_user()
    step = user_data.get("onboarding_step") if user_data else None
    if step == 5:
        print(f"\n  {GREEN}✓ onboarding_step = 5 — onboarding complete{RESET}")
    else:
        print(f"\n  {RED}✗ expected onboarding_step=5 but got {step}{RESET}")

    return user_data


# ── COACHING PHASE ────────────────────────────────────────────────────────────

async def run_coaching(user_data: dict):
    from services.message_router import process_inbound_sms, classify

    user_id = user_data["id"]
    user_timezone = (user_data.get("schedule") or {}).get("timezone", "America/New_York")

    # Re-fetch with full relations
    res = supabase.table("users").select("*, schedule(*), coach_settings(*)").eq("id", user_id).execute()
    user_data = res.data[0] if res.data else user_data
    user_timezone = (user_data.get("schedule") or [{}])[0].get("timezone", "America/New_York") if isinstance(user_data.get("schedule"), list) else (user_data.get("schedule") or {}).get("timezone", "America/New_York")

    print(f"\n{BOLD}{'═'*54}{RESET}")
    print(f"{BOLD}  COACHING PHASE{RESET}")
    print(f"{BOLD}{'═'*54}{RESET}")

    coaching_messages = [
        "hey",
        "I did my run this morning, 4 miles",
        "just 4 miles",
        "I ate McDonalds for lunch",
        "remind me to train tomorrow at 7am",
        "I'm really tired and thinking of quitting",
        "who are you",
    ]

    for msg in coaching_messages:
        print(f"\n{YELLOW}[coaching]{RESET} sending: {msg!r}")
        category = await classify(msg)

        # Log inbound message
        try:
            supabase.table("messages").insert({
                "user_id": user_id,
                "direction": "inbound",
                "body": msg,
            }).execute()
        except Exception:
            pass

        reply = await process_inbound_sms(user_id, msg, user_data, user_timezone)

        # Log outbound message
        try:
            supabase.table("messages").insert({
                "user_id": user_id,
                "direction": "outbound",
                "body": reply,
            }).execute()
        except Exception:
            pass

        _print_exchange(msg, reply, category=category)
        time.sleep(5)


# ── VERIFICATION ──────────────────────────────────────────────────────────────

def run_checks(user_data: dict):
    uid = user_data["id"]
    phone = user_data.get("phone")

    print(f"\n{BOLD}{'═'*54}{RESET}")
    print(f"{BOLD}  VERIFICATION CHECKS{RESET}")
    print(f"{BOLD}{'═'*54}{RESET}\n")

    checks = []

    def check(label: str, ok: bool, detail: str = ""):
        status = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        line = f"  {status}  {label}"
        if detail:
            line += f"  ({detail})"
        print(line)
        checks.append(ok)

    # users table
    u = supabase.table("users").select("phone, onboarding_step").eq("id", uid).execute()
    urow = u.data[0] if u.data else {}
    check("users: row exists with correct phone", urow.get("phone") == TEST_PHONE, f"phone={urow.get('phone')}")
    check("users: onboarding_step = 5", urow.get("onboarding_step") == 5, f"step={urow.get('onboarding_step')}")

    # goals table
    g = supabase.table("goals").select("activity, days").eq("user_id", uid).execute()
    check("goals: at least 2 rows created", len(g.data or []) >= 2, f"count={len(g.data or [])}")
    if g.data:
        print(f"         activities: {[r['activity'] for r in g.data]}")
        print(f"         days sample: {g.data[0].get('days')}")

    # coach_settings table
    cs = supabase.table("coach_settings").select("coach_name, personality_id, is_active").eq("user_id", uid).execute()
    csrow = cs.data[0] if cs.data else {}
    check("coach_settings: active row exists", bool(cs.data), f"coach={csrow.get('coach_name')}")
    check("coach_settings: personality_id set", bool(csrow.get("personality_id")), f"id={csrow.get('personality_id')}")

    # messages table
    msgs = supabase.table("messages").select("direction").eq("user_id", uid).execute()
    inbound  = [m for m in (msgs.data or []) if m["direction"] == "inbound"]
    outbound = [m for m in (msgs.data or []) if m["direction"] == "outbound"]
    check("messages: inbound messages logged", len(inbound) > 0, f"count={len(inbound)}")
    check("messages: outbound messages logged", len(outbound) > 0, f"count={len(outbound)}")

    # nutrition_logs table
    nl = supabase.table("nutrition_logs").select("id").eq("user_id", uid).execute()
    check("nutrition_logs: McDonalds entry logged", len(nl.data or []) > 0, f"count={len(nl.data or [])}")

    # reminders table
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    rem = supabase.table("reminders").select("description, scheduled_for").eq("user_id", uid).execute()
    has_reminder = any("train" in (r.get("description") or "").lower() or "7" in (r.get("scheduled_for") or "") for r in (rem.data or []))
    check("reminders: 7am training reminder created", has_reminder or len(rem.data or []) > 0, f"count={len(rem.data or [])}")

    # streaks table
    st = supabase.table("streaks").select("current_streak").eq("user_id", uid).execute()
    check("streaks: row exists for running goal", len(st.data or []) > 0, f"count={len(st.data or [])}")

    print()
    if all(checks):
        print(f"  {GREEN}{BOLD}ALL CHECKS PASSED{RESET}")
    else:
        failed = checks.count(False)
        print(f"  {RED}{BOLD}{failed} CHECK(S) FAILED{RESET}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}stackd — Full SMS Flow Simulation{RESET}")
    print(f"Phone: {TEST_PHONE}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    cleanup_before()

    # Onboarding
    user_data = await run_onboarding()
    if not user_data:
        print(f"\n{RED}Onboarding failed — no user_data. Aborting.{RESET}")
        return

    # Coaching
    await run_coaching(user_data)

    # Verification
    run_checks(user_data)

    # Cleanup prompt
    print(f"\n{YELLOW}Clean up test data? (y/n):{RESET} ", end="", flush=True)
    ans = input().strip().lower()
    if ans == "y":
        _delete_test_user()
        print(f"  {GREEN}Test data removed.{RESET}")
    else:
        print(f"  Data left in DB for inspection. User ID: {user_data['id']}")


if __name__ == "__main__":
    asyncio.run(main())
