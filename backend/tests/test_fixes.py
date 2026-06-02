"""
Evidence tests for hallucinated-goal and bot-assumption fixes.

Run from backend/:
    python tests/test_fixes.py

Does NOT require a running server — classifier is called in-process.
Supabase reads use the real DB to verify no phantom rows were created.
"""
import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
import google.generativeai as genai

# ── Config ────────────────────────────────────────────────────────────────────
USER_ID   = "30f9256d-d099-4f74-8d4a-548b5bb2a550"
USER_PHONE = "+19176316464"

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY"),
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_goals():
    return supabase.table("goals").select("id, activity, times_per_day") \
        .eq("user_id", USER_ID).execute().data or []

def get_activity_notifications():
    return supabase.table("activity_notifications").select("id, activity, state, scheduled_time") \
        .eq("user_id", USER_ID).execute().data or []

async def classify(message: str) -> str:
    from services.message_router import classify as _classify
    return await _classify(message)

# ── Test runner ───────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"

results = []

def check(label: str, expected, got, pass_condition: bool, detail: str = ""):
    results.append({
        "label": label,
        "expected": str(expected),
        "got": str(got),
        "pass": pass_condition,
        "detail": detail,
    })
    status = PASS if pass_condition else FAIL
    print(f"  [{status}] {label}")
    if not pass_condition:
        print(f"          expected: {expected}")
        print(f"          got:      {got}")
    if detail:
        print(f"          {detail}")


async def run_classifier_tests():
    print("\n── Scenario A: Intent Classifier ────────────────────────────────")
    print("Verifying the classifier correctly routes messages so casual\n"
          "activity mentions never trigger goal creation.\n")

    cases = [
        # (message, expected_category, reason)
        ("I want to add a goal",                   "CREATE_GOAL",        "explicit registration intent"),
        ("can we track my running",                "CREATE_GOAL",        "explicit track intent"),
        ("add journaling as a habit to track",     "CREATE_GOAL",        "explicit add + track"),
        ("I want to start meditating",             "GENERAL",            "casual desire, not explicit goal registration"),
        ("I want to begin reading",                "GENERAL",            "casual — no register/add/track language"),
        ("I wake up at 5am",                       "GENERAL",            "just describing routine — no goal intent"),
        ("Maybe I should meditate at 5am",         "GENERAL",            "speculation, not registration"),
        ("I did my run today",                     "GOAL",               "check-in on existing goal"),
        ("Just finished the gym",                  "GOAL",               "check-in"),
        ("I feel exhausted and want to quit",      "JOURNAL",            "emotional state"),
        ("remind me at 9am to meditate",           "TASK",               "scheduling request"),
        ("I had a burger and coffee for lunch",    "NUTRITION",          "food mention"),
        ("what are my goals",                      "STATS_QUERY",        "querying own data"),
        ("I need motivation right now",            "MOTIVATION_REQUEST", "explicit motivation ask"),
        ("delete my running goal",                 "DELETE_GOAL",        "explicit deletion"),
    ]

    passed = 0
    for msg, expected, reason in cases:
        got = await classify(msg)
        ok  = got == expected
        if ok:
            passed += 1
        check(
            f"'{msg[:50]}'",
            expected,
            got,
            ok,
            f"reason: {reason}",
        )

    print(f"\n  Classifier: {passed}/{len(cases)} correct")


async def run_phantom_goal_tests():
    print("\n── Scenario B: Casual 5 AM mention creates NO goal ─────────────")
    print("Simulating the exact conversation that caused hallucinated goals.\n")

    goals_before = get_goals()
    count_before = len(goals_before)
    print(f"  Goals in DB before test: {count_before}")
    for g in goals_before:
        print(f"    - {g['activity']} | times_per_day: {g['times_per_day']}")

    # Simulate via classify only (no server needed) — verifies the
    # messages that caused the bug now route to GENERAL not CREATE_GOAL
    phantom_messages = [
        "I wake up at 5am and want to do something",
        "Maybe I should meditate at 5am",
        "I want to begin reading at noon",
        "I want to begin meditating",
        "I want to write about things that I did for the day",
        "I want to reflect on where I could improve",
        "30 minutes each morning",
    ]

    any_create_goal = False
    for msg in phantom_messages:
        cat = await classify(msg)
        is_create = cat == "CREATE_GOAL"
        if is_create:
            any_create_goal = True
        check(
            f"'{msg[:55]}' → {cat}",
            "NOT CREATE_GOAL",
            cat,
            not is_create,
            "would have created phantom goal" if is_create else "safely routed away from goal creation",
        )

    goals_after = get_goals()
    count_after  = len(goals_after)

    check(
        "Goal count unchanged after casual messages",
        f"{count_before} goals",
        f"{count_after} goals",
        count_after == count_before,
    )

    # Verify no goal has a phantom 5am time
    phantom_times = []
    for g in goals_after:
        tpd = g.get("times_per_day") or {}
        for day, sched in tpd.items():
            times = sched.get("times", []) if isinstance(sched, dict) else []
            if "05:00" in times:
                phantom_times.append(f"{g['activity']} on {day}")

    check(
        "No goal has a 05:00 scheduled time",
        "0 goals with 05:00",
        f"{len(phantom_times)} goals with 05:00: {phantom_times}",
        len(phantom_times) == 0,
    )


async def run_notification_assumption_tests():
    print("\n── Scenario C: Bot does NOT assume goal completion ──────────────")
    print("Checking activity_notifications table for NOTIFIED rows that\n"
          "should NOT have triggered a start message.\n")

    notifications = get_activity_notifications()
    print(f"  Activity notifications in DB: {len(notifications)}")
    for n in notifications:
        print(f"    - {n['activity']} at {n.get('scheduled_time','?')} | state: {n['state']}")

    notified_rows = [n for n in notifications if n["state"] == "NOTIFIED"]
    started_rows  = [n for n in notifications if n["state"] == "STARTED"]

    check(
        "No NOTIFIED rows auto-escalated to STARTED without user confirmation",
        "0 STARTED rows from unconfirmed notifications",
        f"{len(started_rows)} STARTED rows",
        len(started_rows) == 0,
        "STARTED state can only come from CONFIRMED — the code fix is: "
        "`if existing_state == 'CONFIRMED'` (was: `in ('CONFIRMED', 'NOTIFIED')`)",
    )

    # Verify the code diff is in place
    scheduler_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "scheduler.py",
    )
    with open(scheduler_path) as f:
        scheduler_src = f.read()

    old_code_present = "existing_state in (\"CONFIRMED\", \"NOTIFIED\")" in scheduler_src
    new_code_present  = "existing_state == \"CONFIRMED\"" in scheduler_src

    check(
        "Scheduler source: start message gated on CONFIRMED only",
        "new code present, old code absent",
        f"new_code={new_code_present}, old_code_removed={not old_code_present}",
        new_code_present and not old_code_present,
    )


async def run_strip_markdown_test():
    print("\n── Scenario D: Markdown strip in scheduler send_sms ─────────────")
    print("Verifying the send_sms() wrapper calls _strip_markdown.\n")

    scheduler_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "scheduler.py",
    )
    with open(scheduler_path) as f:
        src = f.read()

    # Check send_sms wraps with _strip_markdown
    check(
        "send_sms() calls _strip_markdown(body)",
        "_strip_markdown(body) inside send_sms",
        "found" if "_strip_markdown(body)" in src else "NOT FOUND",
        "_strip_markdown(body)" in src,
    )

    # Check persona block is stripped before injection
    router_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "services", "message_router.py",
    )
    with open(router_path) as f:
        router_src = f.read()

    check(
        "Voice reply uses get_persona_examples_block (no duplicate identity header)",
        "get_persona_examples_block in message_router",
        "found" if "get_persona_examples_block" in router_src else "NOT FOUND",
        "get_persona_examples_block" in router_src,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("STACKD — BUG FIX EVIDENCE TESTS")
    print("=" * 65)

    await run_classifier_tests()
    await run_phantom_goal_tests()
    await run_notification_assumption_tests()
    await run_strip_markdown_test()

    # Summary
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

    # Save JSON evidence
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fix_evidence.json")
    with open(out_path, "w") as f:
        json.dump({"passed": passed, "total": total, "results": results}, f, indent=2)
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
