"""
tests/test_onboarding.py — Simulate the full SMS onboarding flow.

Runs each step of handle_onboarding() directly without Twilio,
printing what the bot would send at each step.

Run from the backend directory:
    python tests/test_onboarding.py
"""

import asyncio
import os
import sys
import json

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND, ".env"))

from supabase import create_client

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

TEST_PHONE = "+15550000001"  # fake number for test

# ---------------------------------------------------------------------------
# Stub BackgroundTasks — runs tasks inline so we don't need an HTTP context
# ---------------------------------------------------------------------------

class InlineBackgroundTasks:
    def __init__(self):
        self._tasks = []

    def add_task(self, func, *args, **kwargs):
        self._tasks.append((func, args, kwargs))

    async def run_all(self):
        for func, args, kwargs in self._tasks:
            print(f"\n  [background] running {func.__name__}...")
            await func(*args, **kwargs)
        self._tasks.clear()


# ---------------------------------------------------------------------------
# Stub Twilio — capture messages instead of sending
# ---------------------------------------------------------------------------

_sent_messages: list[str] = []

def _patch_twilio():
    import services.onboarding as onb
    class _FakeMessages:
        def create(self, body, from_, to):
            _sent_messages.append(body)
            print(f"  [SMS → {to}] {body}")
    class _FakeTwilio:
        messages = _FakeMessages()
    onb._twilio = _FakeTwilio()

# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------

def cleanup():
    try:
        res = supabase.table("users").select("id").eq("phone", TEST_PHONE).execute()
        if res.data:
            uid = res.data[0]["id"]
            supabase.table("coach_settings").delete().eq("user_id", uid).execute()
            supabase.table("goals").delete().eq("user_id", uid).execute()
            supabase.table("user_context").delete().eq("user_id", uid).execute()
            supabase.table("users").delete().eq("id", uid).execute()
            print(f"  [cleanup] removed test user {uid}")
    except Exception as e:
        print(f"  [cleanup] error: {e}")

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def run():
    from services.onboarding import handle_onboarding

    _patch_twilio()
    cleanup()

    SEP = "─" * 60

    async def step(label, message, user_data, bg_tasks=None):
        if bg_tasks is None:
            bg_tasks = InlineBackgroundTasks()
        print(f"\n{SEP}")
        print(f"USER: {message}")
        reply = await handle_onboarding(TEST_PHONE, message, bg_tasks, supabase, user_data)
        if reply:
            print(f" BOT: {reply}")
        else:
            print(" BOT: (None — falls through to normal pipeline)")
        return reply, bg_tasks

    # ── Step 0: unknown number ───────────────────────────────────────────
    reply, _ = await step("Step 0 — unknown number", "hey", user_data=None)
    assert "coach" in reply.lower(), f"Expected coach prompt, got: {reply}"

    # Fetch the newly created user
    u = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute()
    assert u.data, "User was not created"
    user_data = u.data[0]
    print(f"  [db] user created: {user_data['id']}, step={user_data['onboarding_step']}")

    # ── Step 1: coach name ───────────────────────────────────────────────
    bg = InlineBackgroundTasks()
    reply, _ = await step("Step 1 — coach name", "David Goggins", user_data=user_data, bg_tasks=bg)
    assert reply, "Expected ack reply"

    # Re-fetch step
    user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
    assert user_data["onboarding_step"] == 1, f"Expected step=1, got {user_data['onboarding_step']}"

    # Run background task (persona setup + intro)
    print(f"\n{SEP}")
    print("[Running background task — this may take 15-30s for persona generation...]")
    await bg.run_all()

    user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
    print(f"  [db] after background task: step={user_data['onboarding_step']}")
    assert user_data["onboarding_step"] == 2, f"Expected step=2 after background task, got {user_data['onboarding_step']}"

    coach_res = supabase.table("coach_settings").select("coach_name, personality_id").eq("user_id", user_data["id"]).execute()
    assert coach_res.data, "coach_settings row not found"
    print(f"  [db] coach: {coach_res.data[0]['coach_name']} ({coach_res.data[0]['personality_id']})")

    # ── Step 2: goals ────────────────────────────────────────────────────
    reply, _ = await step("Step 2 — goals", "I want to run 3 miles every week and do 50 pushups daily", user_data=user_data)
    assert reply, "Expected goals reply"

    user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
    assert user_data["onboarding_step"] == 3, f"Expected step=3, got {user_data['onboarding_step']}"
    goals = supabase.table("goals").select("*").eq("user_id", user_data["id"]).execute()
    print(f"  [db] goals saved: {[g['activity'] for g in goals.data]}")
    assert goals.data, "No goals were saved"

    # ── Step 3: schedule loop ────────────────────────────────────────────
    print(f"\n{SEP}")
    # Check how many goals were extracted
    ctx_res = supabase.table("user_context").select("description").eq("user_id", user_data["id"]).eq("type", "onboarding_goals").execute()
    ctx = json.loads(ctx_res.data[0]["description"])
    total_goals = len(ctx["goals"])
    print(f"  [db] {total_goals} goal(s) to schedule")

    for i in range(total_goals):
        user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
        reply, _ = await step(f"Step 3.{i} — schedule for goal {i+1}", "Mon Wed Fri", user_data=user_data)
        assert reply, "Expected schedule reply"

    user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
    assert user_data["onboarding_step"] == 4, f"Expected step=4 after all goals, got {user_data['onboarding_step']}"

    # ── Step 4: final handoff ─────────────────────────────────────────────
    reply, _ = await step("Step 4 — final handoff", "ok", user_data=user_data)
    assert reply, "Expected final reply"

    user_data = supabase.table("users").select("*").eq("phone", TEST_PHONE).execute().data[0]
    assert user_data["onboarding_step"] == 5, f"Expected step=5, got {user_data['onboarding_step']}"

    # ── Step 5: falls through ─────────────────────────────────────────────
    reply, _ = await step("Step 5 — normal pipeline", "hey", user_data=user_data)
    assert reply is None, f"Expected None (fall-through), got: {reply}"

    print(f"\n{SEP}")
    print("ALL STEPS PASSED")
    print(f"  Background SMS messages sent: {len(_sent_messages)}")
    for m in _sent_messages:
        print(f"    • {m[:80]}")

    cleanup()


if __name__ == "__main__":
    asyncio.run(run())
