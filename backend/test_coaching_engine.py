"""
test_coaching_engine.py

End-to-end verification of CoachingContext injection and Inquiry Mode.

What this does:
  1. Seeds a test user directly in Supabase
  2. Inserts a goal + a broken streak (last_checkin = 3 days ago)
  3. Calls get_coaching_context() — prints the full prompt block and anomaly state
  4. Calls process_inbound_sms() — prints the full system prompt sent to Gemini
     and the final response
  5. Cleans up the test user

Run from backend/:
    python3 test_coaching_engine.py
"""

import asyncio
import os
import sys
import uuid
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

# Add backend to path so service imports resolve
sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

# ---------------------------------------------------------------------------
# Patch process_inbound_sms to capture the system prompt before it hits Gemini
# ---------------------------------------------------------------------------

_captured_system_prompt: str = ""

def _patch_genai():
    """
    Monkey-patch GenerativeModel so we can intercept the system_instruction
    that process_inbound_sms sends to Gemini, without needing a real API call.
    We still let the real call through — we just capture the prompt first.
    """
    import google.generativeai as genai
    _original_init = genai.GenerativeModel.__init__

    def _patched_init(self, *args, **kwargs):
        global _captured_system_prompt
        _captured_system_prompt = kwargs.get("system_instruction", "")
        _original_init(self, *args, **kwargs)

    genai.GenerativeModel.__init__ = _patched_init

_patch_genai()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_run_id    = str(uuid.uuid4())[:8]
TEST_EMAIL = f"coaching-test-{_run_id}@dev.local"
TEST_USER_ID: str = ""   # filled in by seed_user() after auth creates the row

def seed_user() -> str:
    global TEST_USER_ID
    print(f"\n{'='*60}")

    # Let Supabase auth generate the UUID — public.users has FK to auth.users(id)
    auth_resp = supabase.auth.admin.create_user({
        "email": TEST_EMAIL,
        "password": "test-only-throwaway",
        "email_confirm": True,
    })
    TEST_USER_ID = auth_resp.user.id
    print(f"Seeding test user: {TEST_USER_ID}")

    supabase.table("users").insert({
        "id": TEST_USER_ID,
        "email": TEST_EMAIL,
        "name": "Test Runner",
    }).execute()

    supabase.table("coach_settings").insert({
        "user_id": TEST_USER_ID,
        "coach_name": "Test Coach",
        "generated_system_prompt": (
            "You are a no-nonsense accountability coach. "
            "You speak directly, ask hard questions, and never accept excuses."
        ),
        "is_active": True,
    }).execute()

    supabase.table("schedule").insert({
        "user_id": TEST_USER_ID,
        "timezone": "America/New_York",
        "checkin_time": "07:00",
    }).execute()

    # Goal scheduled every day
    goal_res = supabase.table("goals").insert({
        "user_id": TEST_USER_ID,
        "activity": "Morning Run",
        "category": "Fitness & Sports",
        "days": ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"],
    }).execute()
    goal_id = goal_res.data[0]["id"]

    # Broken streak: last check-in was 3 days ago
    broken_date = (date.today() - timedelta(days=3)).isoformat()
    supabase.table("streaks").insert({
        "user_id": TEST_USER_ID,
        "goal_id": goal_id,
        "current_streak": 7,
        "longest_streak": 14,
        "last_checkin": broken_date,
    }).execute()

    print(f"  ✓ User, coach settings, schedule created")
    print(f"  ✓ Goal 'Morning Run' (every day)")
    print(f"  ✓ Streak: 7-day streak, last check-in = {broken_date} (BROKEN)")


def cleanup_user():
    print(f"\nCleaning up test user {TEST_USER_ID} …")
    for table in ["streaks", "goals", "reminders", "user_context", "messages",
                  "coach_settings", "schedule"]:
        try:
            supabase.table(table).delete().eq("user_id", TEST_USER_ID).execute()
        except Exception as e:
            print(f"  warn: cleanup {table} — {e}")
    try:
        supabase.table("users").delete().eq("id", TEST_USER_ID).execute()
    except Exception as e:
        print(f"  warn: cleanup users — {e}")
    try:
        supabase.auth.admin.delete_user(TEST_USER_ID)
    except Exception as e:
        print(f"  warn: cleanup auth user — {e}")
    print("  ✓ Done")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_coaching_context():
    from services.coaching_service import get_coaching_context

    print(f"\n{'='*60}")
    print("STEP 1 — get_coaching_context()")
    print('='*60)

    ctx = await get_coaching_context(TEST_USER_ID, "America/New_York")

    fitness = ctx.provider_data.get("fitness", {})
    print(f"has_anomaly     : {ctx.has_anomaly}")
    print(f"anomaly_reasons : {ctx.anomaly_reasons}")
    print(f"goals_due_today : {fitness.get('goals_due_today')}")
    print(f"goals_missed    : {fitness.get('missed_today')}")
    print(f"broken_streaks  : {fitness.get('broken_streaks')}")
    print(f"\n--- Prompt Block ---\n{ctx.to_prompt_block()}")

    assert ctx.has_anomaly, "FAIL: has_anomaly should be True (broken streak detected)"
    assert any("Morning Run" in r or "streak" in r for r in ctx.anomaly_reasons), \
        "FAIL: anomaly_reasons should mention the broken streak"
    print("\n✅ PASS: anomaly correctly detected")
    return ctx


async def test_full_pipeline():
    from services.message_router import process_inbound_sms, INQUIRY_MODE_INSTRUCTION

    print(f"\n{'='*60}")
    print("STEP 2 — process_inbound_sms()")
    print('='*60)

    user_data = {
        "id": TEST_USER_ID,
        "name": "Test Runner",
        "schedule": {"timezone": "America/New_York"},
    }

    test_message = "Hey, what's up?"

    print(f"Sending message: '{test_message}'")
    reply = await process_inbound_sms(
        user_id=TEST_USER_ID,
        message_body=test_message,
        user_data=user_data,
        user_timezone="America/New_York",
    )

    print(f"\n--- System Prompt Sent to Gemini ---\n{_captured_system_prompt}\n")
    print(f"--- Model Reply ---\n{reply}\n")

    # Verify Inquiry Mode was injected
    inquiry_trigger = "INQUIRY MODE" in _captured_system_prompt
    context_injected = "COACHING CONTEXT" in _captured_system_prompt
    anomaly_flagged = "DATA ANOMALY" in _captured_system_prompt

    print("--- Verification ---")
    print(f"  CoachingContext block injected : {'✅' if context_injected else '❌'}")
    print(f"  DATA ANOMALY line present      : {'✅' if anomaly_flagged  else '❌'}")
    print(f"  INQUIRY MODE injected          : {'✅' if inquiry_trigger  else '❌'}")

    assert context_injected, "FAIL: CoachingContext block missing from system prompt"
    assert anomaly_flagged,  "FAIL: DATA ANOMALY signal missing from system prompt"
    assert inquiry_trigger,  "FAIL: INQUIRY MODE instruction missing from system prompt"

    print("\n✅ PASS: full pipeline correct — Inquiry Mode active in prompt")
    print("\n--- Check the reply above manually ---")
    print("It should: call out the broken streak, NOT offer generic motivation,")
    print("and end with exactly ONE root-cause question.")


# ---------------------------------------------------------------------------
# Feedback loop test
# ---------------------------------------------------------------------------

async def test_feedback_loop():
    from services.coaching_service import save_coach_insight, get_coaching_context
    from services.message_router import _looks_like_inquiry_question, _maybe_save_inquiry_answer

    print(f"\n{'='*60}")
    print("STEP 3 — Feedback Loop (inquiry → answer → insight → context)")
    print('='*60)

    # ── 3a. Verify heuristic recognises an inquiry question ──────────────────
    question = "Wait, your run? You didn't log it. What happened this morning?"
    not_question = "Let's get it tomorrow, keep pushing."

    assert _looks_like_inquiry_question(question), \
        "FAIL: heuristic should detect inquiry question"
    assert not _looks_like_inquiry_question(not_question), \
        "FAIL: heuristic should NOT flag a non-question"
    print("  ✓ heuristic correctly identifies inquiry questions")

    # ── 3b. Simulate: bot asked a question, now user answers ─────────────────
    # Plant the bot's question as the last outbound message in the DB
    bot_question = "You missed your morning run. What got in the way today?"
    supabase.table("messages").insert({
        "user_id":   TEST_USER_ID,
        "direction": "outbound",
        "body":      bot_question,
    }).execute()

    user_answer = "I stayed up too late last night and just couldn't get up."

    # Run auto-save directly (bypasses full Gemini call)
    await _maybe_save_inquiry_answer(TEST_USER_ID, user_answer)
    print(f"  ✓ _maybe_save_inquiry_answer called with: '{user_answer}'")

    # ── 3c. Verify the insight landed in user_context ────────────────────────
    res = (
        supabase.table("user_context")
        .select("type, description")
        .eq("user_id", TEST_USER_ID)
        .eq("type", "coach_insight")
        .execute()
    )
    assert res.data, "FAIL: no coach_insight row found in user_context"

    saved_description = res.data[0]["description"]
    assert user_answer[:30] in saved_description, \
        f"FAIL: saved insight doesn't contain the user's answer. Got: {saved_description}"
    print(f"  ✓ coach_insight saved: '{saved_description[:80]}…'")

    # ── 3d. Verify it surfaces in [CONTEXT_DATA] on the next turn ─────────────
    ctx = await get_coaching_context(TEST_USER_ID, "America/New_York")
    prompt_block = ctx.to_prompt_block()

    assert "CONTEXT_DATA" in prompt_block, \
        "FAIL: [CONTEXT_DATA] block missing from prompt"
    assert "coach_insight" in prompt_block.lower() or user_answer[:20].lower() in prompt_block.lower(), \
        "FAIL: saved coach_insight not appearing in [CONTEXT_DATA] block"

    print(f"  ✓ coach_insight visible in [CONTEXT_DATA] on next turn")
    print(f"\n--- Prompt block excerpt ---")
    for line in prompt_block.split("\n"):
        if "CONTEXT" in line or "insight" in line.lower() or user_answer[:15].lower() in line.lower():
            print(f"    {line}")

    print("\n✅ PASS: full feedback loop verified")


# ---------------------------------------------------------------------------
# Dispatcher + strict constraint test
# ---------------------------------------------------------------------------

async def test_dispatcher():
    from services.message_router import (
        resolve_intent,
        dispatch,
        CheckInHandler,
        TodoHandler,
        JournalHandler,
        QuestionHandler,
        GeneralChatHandler,
        _HANDLER_MAP,
    )
    from services.coaching_service import get_coaching_context

    print(f"\n{'='*60}")
    print("STEP 4 — Dispatcher & Strict Constraint")
    print('='*60)

    # ── 4a. Handler map is complete ──────────────────────────────────────────
    for intent in ("check-in", "to-do", "journal", "question", "general_chat"):
        assert intent in _HANDLER_MAP, f"FAIL: no handler registered for '{intent}'"
    print("  ✓ all five intents have registered handlers")

    # ── 4b. Each handler owns the right intent string ─────────────────────────
    assert isinstance(_HANDLER_MAP["check-in"],    CheckInHandler)
    assert isinstance(_HANDLER_MAP["to-do"],       TodoHandler)
    assert isinstance(_HANDLER_MAP["journal"],     JournalHandler)
    assert isinstance(_HANDLER_MAP["question"],    QuestionHandler)
    assert isinstance(_HANDLER_MAP["general_chat"],GeneralChatHandler)
    print("  ✓ handler types are correct")

    # ── 4c. Non-general_chat intents pass through resolve_intent unchanged ────
    ctx = await get_coaching_context(TEST_USER_ID, "America/New_York")
    for intent in ("check-in", "to-do", "journal", "question"):
        resolved, redirected = resolve_intent(intent, ctx)
        assert resolved == intent,    f"FAIL: {intent} should not be redirected"
        assert not redirected,        f"FAIL: {intent} should not set redirected flag"
    print("  ✓ non-general_chat intents pass through unchanged")

    # ── 4d. Strict constraint fires on general_chat + anomaly ─────────────────
    # Our test user has a broken streak → has_anomaly=True → missed_today is set
    assert ctx.has_anomaly, "FAIL: test user should have anomaly (broken streak)"

    resolved, redirected = resolve_intent("general_chat", ctx)
    assert resolved   == "check-in", f"FAIL: expected check-in, got {resolved}"
    assert redirected == True,       "FAIL: redirected flag should be True"
    print(f"  ✓ strict constraint: general_chat → check-in (redirected=True)")

    # ── 4e. Strict constraint does NOT fire when user is clean ────────────────
    # Simulate a clean context by using a fresh user with no anomalies
    # We can't create another auth user cheaply, so instead check via a mock ctx
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _FakeCtx:
        has_anomaly:   bool = False
        anomaly_score: int  = 0
        provider_data: dict = dc_field(default_factory=dict)

    clean_ctx = _FakeCtx(provider_data={"reminders": {"overdue": []}, "fitness": {"missed_today": []}})
    resolved_clean, redirected_clean = resolve_intent("general_chat", clean_ctx)
    assert resolved_clean   == "general_chat", "FAIL: clean user should stay general_chat"
    assert redirected_clean == False,          "FAIL: clean user should not be redirected"
    print("  ✓ strict constraint does NOT fire when user has no gaps")

    # ── 4f. dispatch() fires the right handler without crashing ───────────────
    # dispatch is fire-and-forget; we just verify it doesn't raise
    dispatch("check-in",   TEST_USER_ID, "did my run", "America/New_York")
    dispatch("to-do",      TEST_USER_ID, "remind me tomorrow at 8am", "America/New_York")
    dispatch("journal",    TEST_USER_ID, "feeling tired today", "America/New_York")
    dispatch("question",   TEST_USER_ID, "what should i eat?", "America/New_York")
    dispatch("general_chat", TEST_USER_ID, "hey", "America/New_York")
    await asyncio.sleep(0.5)  # let background tasks drain
    print("  ✓ dispatch() fired all five handlers without error")

    print("\n✅ PASS: dispatcher and strict constraint work correctly")


# ---------------------------------------------------------------------------
# Data logging & retrieval test
# ---------------------------------------------------------------------------

async def test_nutrition_logging():
    from datetime import date, timezone
    from services.coaching_service import (
        log_nutrition,
        get_coaching_context,
        DatabaseLoggingError,
    )

    print(f"\n{'='*60}")
    print("STEP 5 — Data Logging & Retrieval (Nutrition)")
    print('='*60)

    # ── 5a. Atomic log insert returns a timestamp ─────────────────────────────
    today = date.today()
    created_at = await log_nutrition(
        user_id=TEST_USER_ID,
        calories=620,
        food_description="Grilled chicken salad with avocado",
        reporting_date=today,
    )
    assert isinstance(created_at, str) and len(created_at) > 10, \
        f"FAIL: expected ISO timestamp string, got: {created_at!r}"
    print(f"  ✓ log_nutrition returned created_at: {created_at[:19]}")

    # ── 5b. Log a second meal to test aggregation ─────────────────────────────
    await log_nutrition(
        user_id=TEST_USER_ID,
        calories=350,
        food_description="Greek yogurt with berries",
        reporting_date=today,
    )
    print("  ✓ second meal logged (total should be 970 kcal)")

    # ── 5c. get_coaching_context surfaces values in [NUTRITION_DATA] ──────────
    ctx = await get_coaching_context(TEST_USER_ID, "America/New_York")
    prompt_block = ctx.to_prompt_block()

    assert "NUTRITION_DATA" in prompt_block, \
        "FAIL: [NUTRITION_DATA] block missing from prompt"

    nutrition_meta = ctx.provider_data.get("nutrition", {})
    assert nutrition_meta.get("total_kcal") == 970, \
        f"FAIL: expected 970 kcal total, got {nutrition_meta.get('total_kcal')}"
    assert nutrition_meta.get("meal_count") == 2, \
        f"FAIL: expected 2 meals, got {nutrition_meta.get('meal_count')}"
    assert nutrition_meta.get("reporting_date") == today.isoformat(), \
        f"FAIL: reporting_date mismatch: {nutrition_meta.get('reporting_date')}"

    print(f"  ✓ total_kcal={nutrition_meta['total_kcal']}, meals={nutrition_meta['meal_count']}")
    print(f"  ✓ reporting_date={nutrition_meta['reporting_date']} (user timezone date)")

    # ── 5d. Calorie values appear in the prompt text ──────────────────────────
    assert "970" in prompt_block, \
        "FAIL: total calorie count not present in prompt block"
    assert "Grilled chicken" in prompt_block or "Greek yogurt" in prompt_block, \
        "FAIL: food descriptions not present in prompt block"
    print("  ✓ calorie total and food descriptions visible in [NUTRITION_DATA]")

    # ── 5e. DatabaseLoggingError raised on bad user_id ────────────────────────
    try:
        await log_nutrition(
            user_id="00000000-0000-0000-0000-000000000000",  # non-existent FK
            calories=500,
            food_description="Test food",
            reporting_date=today,
        )
        assert False, "FAIL: expected DatabaseLoggingError but no exception raised"
    except DatabaseLoggingError:
        print("  ✓ DatabaseLoggingError raised correctly on DB failure")

    # ── 5f. Verify user_context was NOT used for nutrition data ───────────────
    context_meta = ctx.provider_data.get("context", {})
    for entry in context_meta.get("entries", []):
        assert "kcal" not in entry.get("description", "").lower() \
               and "calories" not in entry.get("description", "").lower(), \
            "FAIL: calorie data found in user_context — must use nutrition_logs table"
    print("  ✓ user_context contains no calorie data (correct table separation)")

    print("\n--- Prompt block (nutrition section) ---")
    for line in prompt_block.split("\n"):
        if any(k in line for k in ("NUTRITION", "kcal", "calorie", "Grilled", "Greek", "meal")):
            print(f"    {line}")

    print("\n✅ PASS: atomic logging, reporting_date grouping, and retrieval verified")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    seed_user()  # populates TEST_USER_ID
    try:
        await test_coaching_context()
        await test_full_pipeline()
        await test_feedback_loop()
        await test_dispatcher()
        await test_nutrition_logging()
        print(f"\n{'='*60}")
        print("ALL ASSERTIONS PASSED")
        print('='*60)
    except AssertionError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n💥 Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup_user()


if __name__ == "__main__":
    asyncio.run(main())
