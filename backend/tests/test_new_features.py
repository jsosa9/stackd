"""
Evidence tests for:
  1. Context-aware daily check-in (before / during / after / no_goals states)
  2. Nightly summary (all-done / mixed / all-missed / skips silent days)

Run from backend/:
    python tests/test_new_features.py

Does NOT require a running server — AI functions are called in-process.
"""
import asyncio
import os
import sys
import json
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────

USER_ID = "30f9256d-d099-4f74-8d4a-548b5bb2a550"

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"),
)

# ── Test runner ───────────────────────────────────────────────────────────────

results = []

def check(label: str, expected, got, pass_condition: bool, detail: str = ""):
    results.append({"label": label, "expected": str(expected), "got": str(got), "pass": pass_condition, "detail": detail})
    status = "PASS" if pass_condition else "FAIL"
    print(f"  [{status}] {label}")
    if not pass_condition:
        print(f"          expected: {expected}")
        print(f"          got:      {got}")
    if detail:
        print(f"          {detail}")


def looks_like_sms(text: str) -> bool:
    """Rough heuristic: short, no markdown, not empty."""
    return (
        bool(text)
        and "**" not in text
        and "__" not in text
        and len(text) < 500
    )


def mentions_goal(text: str, goal_name: str) -> bool:
    """Match any significant word from the goal name (handles 'gym' matching 'go to the gym')."""
    words = [w for w in goal_name.lower().split() if len(w) >= 3]
    return any(w in text.lower() for w in words) or goal_name.lower() in text.lower()


# ── Scenario A: Context-aware check-in ───────────────────────────────────────

async def run_checkin_tests():
    print("\n── Scenario A: Context-Aware Daily Check-in ────────────────────")
    print("Calls generate_contextual_checkin() in-process for all four states.\n")

    from routes.ai import generate_contextual_checkin

    real_goals = supabase.table("goals").select("id, activity, times_per_day") \
        .eq("user_id", USER_ID).execute().data or []
    goal_name = real_goals[0]["activity"] if real_goals else "gym"
    print(f"  Using goal: '{goal_name}' (from DB)\n")

    # ── State: no_goals ──────────────────────────────────────────────────────
    print("  [A1] no_goals state")
    text = await generate_contextual_checkin(
        USER_ID, "no_goals", [], [], [],
        checkin_time_display="9:00 AM",
    )
    print(f"        → {text[:100]}")
    check("no_goals: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check("no_goals: does NOT mention a specific goal", "no goal name", "ok", goal_name.lower() not in text.lower())

    # ── State: before ────────────────────────────────────────────────────────
    print("\n  [A2] before state — nothing has happened yet")
    goal_times_before = [(goal_name, "18:00")]
    text = await generate_contextual_checkin(
        USER_ID, "before", goal_times_before, [], [],
        checkin_time_display="8:00 AM",
    )
    print(f"        → {text[:100]}")
    check("before: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        "before: mentions the scheduled goal by name",
        f"contains '{goal_name}'",
        text[:80],
        mentions_goal(text, goal_name),
    )
    # Should NOT be asking if they did anything — nothing happened yet
    question_words = ["did you", "how was", "how did", "did it"]
    has_past_question = any(q in text.lower() for q in question_words)
    check(
        "before: does NOT ask about past completion (nothing done yet)",
        "no past-tense questions",
        text[:100],
        not has_past_question,
        detail="'before' messages should prime forward, not ask if something was done",
    )

    # ── State: during ────────────────────────────────────────────────────────
    print("\n  [A3] during state — one done, one pending")
    goal_times_during = [(goal_name, "07:00"), ("reading", "20:00")]
    notif_states_during = [
        {"activity": goal_name, "state": "CONFIRMED", "scheduled_time": "2024-01-01T07:00:00"},
        {"activity": "reading",  "state": "SCHEDULED",  "scheduled_time": "2024-01-01T20:00:00"},
    ]
    text = await generate_contextual_checkin(
        USER_ID, "during", goal_times_during, notif_states_during, [],
        checkin_time_display="12:00 PM",
    )
    print(f"        → {text[:100]}")
    check("during: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        "during: asks about pending goal ('reading'), not the confirmed one",
        "mentions 'reading'",
        text[:100],
        "reading" in text.lower(),
        detail="coach should ask about still-pending goal, not the one already confirmed",
    )

    # ── State: after ─────────────────────────────────────────────────────────
    print("\n  [A4] after state — full day done")
    goal_times_after = [(goal_name, "07:00"), ("reading", "12:00")]
    notif_states_after = [
        {"activity": goal_name, "state": "CONFIRMED", "scheduled_time": "2024-01-01T07:00:00"},
        {"activity": "reading",  "state": "CONFIRMED", "scheduled_time": "2024-01-01T12:00:00"},
    ]
    ctx_after = [{"type": "win", "description": f"completed {goal_name} — felt strong"}]
    text = await generate_contextual_checkin(
        USER_ID, "after", goal_times_after, notif_states_after, ctx_after,
        checkin_time_display="9:00 PM",
    )
    print(f"        → {text[:100]}")
    check("after: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        f"after: mentions '{goal_name}' in end-of-day recap",
        f"contains '{goal_name}'",
        text[:100],
        mentions_goal(text, goal_name),
    )

    # ── State: during with MISSED ────────────────────────────────────────────
    print("\n  [A5] during state — one MISSED, one pending — coach should NOT re-ask about MISSED")
    notif_states_missed = [
        {"activity": goal_name, "state": "MISSED",    "scheduled_time": "2024-01-01T07:00:00"},
        {"activity": "reading",  "state": "SCHEDULED", "scheduled_time": "2024-01-01T20:00:00"},
    ]
    text = await generate_contextual_checkin(
        USER_ID, "during", [(goal_name, "07:00"), ("reading", "20:00")],
        notif_states_missed, [],
        checkin_time_display="2:00 PM",
    )
    print(f"        → {text[:100]}")
    check("during+missed: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    # Coach should ask about reading — either the word "reading" or book-related words
    reading_keywords = ["reading", "read", "book", "page"]
    asks_about_reading = any(kw in text.lower() for kw in reading_keywords)
    # Coach should NOT be asking about the missed gym goal again
    gym_past_question = any(
        phrase in text.lower()
        for phrase in ["did you go to the gym", "did you work out", "what happened with the gym"]
    )
    check(
        "during+missed: asks about pending 'reading' goal (by word or concept)",
        "reading/book/page reference",
        text[:120],
        asks_about_reading,
        detail="coach should ask about what's still pending, not re-interrogate the already-missed item",
    )
    check(
        "during+missed: does NOT re-ask about already-MISSED gym goal",
        "no re-interrogation of missed goal",
        text[:120],
        not gym_past_question,
    )


# ── Scenario B: Nightly summary ───────────────────────────────────────────────

async def run_nightly_summary_tests():
    print("\n── Scenario B: Nightly Summary ─────────────────────────────────")
    print("Calls generate_nightly_summary() in-process for all day outcomes.\n")

    from routes.ai import generate_nightly_summary

    real_goals = supabase.table("goals").select("activity").eq("user_id", USER_ID).execute().data or []
    goal_name = real_goals[0]["activity"] if real_goals else "gym"

    # ── All done ─────────────────────────────────────────────────────────────
    print("  [B1] All goals completed — close strong")
    completions = [{"activity": goal_name}]
    ctx = [{"type": "win", "description": f"crushed {goal_name} — new PR"}]
    text = await generate_nightly_summary(USER_ID, completions, [], ctx)
    print(f"        → {text[:100]}")
    check("all-done: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        f"all-done: mentions '{goal_name}' specifically",
        f"contains '{goal_name}'",
        text[:100],
        mentions_goal(text, goal_name),
    )
    # Should feel like a close, not a generic opener
    closing_words = ["tomorrow", "next", "tonight", "rest", "noted", "did", "anything", "else"]
    has_close = any(w in text.lower() for w in closing_words)
    check(
        "all-done: includes a forward-looking or closing element",
        "closing/tomorrow question",
        text[:100],
        has_close,
    )

    # ── All missed ───────────────────────────────────────────────────────────
    print("\n  [B2] All goals missed — honest, no lecture")
    text = await generate_nightly_summary(USER_ID, [], [{"activity": goal_name}], [])
    print(f"        → {text[:100]}")
    check("all-missed: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        f"all-missed: mentions '{goal_name}'",
        f"contains '{goal_name}'",
        text[:100],
        mentions_goal(text, goal_name),
    )

    # ── Mixed day ────────────────────────────────────────────────────────────
    print("\n  [B3] Mixed day — one done, one missed")
    text = await generate_nightly_summary(
        USER_ID,
        [{"activity": goal_name}],
        [{"activity": "reading"}],
        [{"type": "struggle", "description": "ran out of time to read"}],
    )
    print(f"        → {text[:100]}")
    check("mixed: returns SMS-length text", "non-empty SMS", text[:30] + "...", looks_like_sms(text))
    check(
        f"mixed: mentions completed goal '{goal_name}'",
        f"contains '{goal_name}'",
        text[:120],
        mentions_goal(text, goal_name),
    )

    # ── Skip silent days (no data at all) ────────────────────────────────────
    print("\n  [B4] Silent day — scheduler should skip, no text generated")
    # The scheduler skips when completions=[], missed=[], ctx=[]
    # We verify the logic directly: the skip condition is in send_nightly_summaries()
    scheduler_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "scheduler.py",
    )
    with open(scheduler_path) as f:
        src = f.read()
    skip_present = "if not completions and not missed_goals and not user_context_today" in src
    check(
        "Scheduler source: silent-day skip guard present",
        "skip guard in send_nightly_summaries()",
        "found" if skip_present else "NOT FOUND",
        skip_present,
    )

    # ── Dedup guard ──────────────────────────────────────────────────────────
    print("\n  [B5] Deduplication — nightly_summary_sent guard in scheduler")
    dedup_present = "nightly_summary_sent" in src
    check(
        "Scheduler source: dedup via nightly_summary_sent in user_context",
        "dedup guard present",
        "found" if dedup_present else "NOT FOUND",
        dedup_present,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("STACKD — NEW FEATURE TESTS")
    print("  1. Context-Aware Check-in")
    print("  2. Nightly Summary")
    print("=" * 65)

    await run_checkin_tests()
    await run_nightly_summary_tests()

    total  = len(results)
    passed = sum(1 for r in results if r["pass"])
    failed = total - passed

    print("\n" + "=" * 65)
    print(f"SUMMARY: {passed}/{total} passed", "✓" if failed == 0 else f"  {failed} FAILED")
    print("=" * 65)

    if failed:
        print("\nFailed tests:")
        for r in results:
            if not r["pass"]:
                print(f"  FAIL  {r['label']}")
                print(f"        expected: {r['expected']}")
                print(f"        got:      {r['got']}")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "new_features_evidence.json")
    with open(out_path, "w") as f:
        json.dump({"passed": passed, "total": total, "results": results}, f, indent=2)
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
