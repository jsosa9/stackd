"""
coaching_service.py — The Brain.

Architecture: Provider Pattern
-------------------------------
Each data domain owns a ContextProvider subclass. Providers are self-contained:
they fetch their own data, produce their own labeled prompt block, score their
own anomalies, and expose raw metadata for cross-provider use.

get_coaching_context() runs all registered providers concurrently, merges their
ProviderResult objects into a CoachingContext, and returns it.

Prompt structure injected into Gemini:
    === COACHING CONTEXT ===
    [FITNESS_DATA]   ...   [/FITNESS_DATA]
    [CONTEXT_DATA]   ...   [/CONTEXT_DATA]
    [REMINDER_DATA]  ...   [/REMINDER_DATA]
    [NUTRITION_DATA] ...   [/NUTRITION_DATA]
    ⚠ DATA ANOMALY ...
    === END COACHING CONTEXT ===

Logging rules:
- Strongly-typed tables (nutrition_logs, sleep_logs) use reporting_date DATE
  for daily grouping — never raw timestamp arithmetic in the SELECT.
- user_context is for unstructured signals only (mood, journal, insights).
  Calorie counts, sleep hours, macros must NOT go into user_context.

Data retrieval rule:
  All provider SELECT queries that aggregate by day must filter on
  reporting_date = <today in user timezone>, not on created_at ranges.
  This survives late-night logging and timezone edge cases correctly.

Atomic logging:
  Every log_* function returns the created_at timestamp on success and
  raises DatabaseLoggingError on failure so the caller can notify the user.

To add a new data domain:
    1. Subclass ContextProvider, implement fetch().
    2. Add an instance to PROVIDERS.
    Nothing else changes.

Public API:
    get_coaching_context(user_id, user_timezone) -> CoachingContext
    save_coach_insight(user_id, insight_text)    -> None
    log_nutrition(user_id, calories, food_description, reporting_date, ...) -> str
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytz
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger(__name__)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


# ---------------------------------------------------------------------------
# Custom exception — raised by log_* functions on DB failure
# ---------------------------------------------------------------------------

class DatabaseLoggingError(Exception):
    """
    Raised when a structured log insert fails.
    message_router catches this and informs the user that the log was not saved.
    Wraps the original exception as __cause__ for full traceback visibility.
    """


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """
    What each provider returns after fetching its domain data.

    prompt_lines    : content lines that go inside this provider's labeled block.
                      Rendered as [PROVIDER_LABEL_DATA] ... [/PROVIDER_LABEL_DATA].
                      Empty list → block is omitted entirely.
    anomaly_score   : 0 = clean  |  1 = mild  |  2 = moderate  |  3 = critical
    anomaly_reasons : plain-English descriptions (one per detected issue).
    metadata        : raw domain data for cross-provider use and callers.
    """
    prompt_lines:   list[str] = field(default_factory=list)
    anomaly_score:  int        = 0
    anomaly_reasons: list[str] = field(default_factory=list)
    metadata:        dict      = field(default_factory=dict)


class ContextProvider(ABC):
    """
    Base class for all coaching data providers.

    Subclasses must implement name (str property) and fetch().
    They must never raise — catch all exceptions internally and return a
    partial/empty result so one slow/broken provider never halts the pipeline.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'fitness'. Used as the prompt block label."""

    @property
    def prompt_label(self) -> str:
        """Label used in the prompt block, e.g. 'FITNESS_DATA'."""
        return f"{self.name.upper()}_DATA"

    @abstractmethod
    async def fetch(self, user_id: str, params: dict) -> ProviderResult:
        """
        Fetch domain data for user_id and return a ProviderResult.

        params (pre-computed shared values passed by get_coaching_context):
            today_name : lowercase weekday, e.g. "monday"
            yesterday  : datetime.date of yesterday (UTC)
            now_utc    : timezone-aware datetime of now (UTC)
        """


# ---------------------------------------------------------------------------
# CoachingContext — aggregated output
# ---------------------------------------------------------------------------

@dataclass
class CoachingContext:
    """
    Merged snapshot built from all provider results.

    provider_blocks  : dict mapping provider.name → list[str] of content lines.
    anomaly_score    : sum of all provider anomaly_scores.
    has_anomaly      : True when aggregated anomaly_score > 0.
    anomaly_reasons  : merged list from all providers.
    provider_data    : raw metadata keyed by provider name.
    """
    provider_blocks:  dict      = field(default_factory=dict)   # {name: [lines]}
    provider_labels:  dict      = field(default_factory=dict)   # {name: prompt_label}
    anomaly_score:    int       = 0
    has_anomaly:      bool      = False
    anomaly_reasons:  list[str] = field(default_factory=list)
    provider_data:    dict      = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """
        Render the full coaching context string for injection into the system
        prompt. Each provider's data appears in its own labeled XML-style block
        so the LLM can reference them cleanly by name.
        """
        parts: list[str] = ["=== COACHING CONTEXT (live data — do not contradict) ==="]

        for provider_name, lines in self.provider_blocks.items():
            if not lines:
                continue
            label = self.provider_labels.get(provider_name, f"{provider_name.upper()}_DATA")
            block_lines = [f"[{label}]"] + lines + [f"[/{label}]"]
            parts.append("\n".join(block_lines))

        if self.has_anomaly:
            score_label = {1: "MILD", 2: "MODERATE", 3: "CRITICAL"}.get(
                min(self.anomaly_score, 3), "CRITICAL"
            )
            reasons = "; ".join(self.anomaly_reasons)
            parts.append(
                f"⚠ DATA ANOMALY DETECTED ({score_label}, score={self.anomaly_score}): {reasons}\n"
                "→ INQUIRY MODE ACTIVE: identify root cause, ask ONE targeted question."
            )

        parts.append("=== END COACHING CONTEXT ===")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# FitnessProvider — goals, streaks, missed check-ins
# ---------------------------------------------------------------------------

class FitnessProvider(ContextProvider):

    @property
    def name(self) -> str:
        return "fitness"

    async def fetch(self, user_id: str, params: dict) -> ProviderResult:
        today_name = params["today_name"]
        yesterday  = params["yesterday"]
        result     = ProviderResult()

        # Goals
        try:
            goals_res = (
                supabase.table("goals")
                .select("id, activity, days")
                .eq("user_id", user_id)
                .execute()
            )
            goals = goals_res.data or []
        except Exception:
            logger.exception(f"[FitnessProvider] goals fetch failed for {user_id}")
            return result

        due_today = [g["activity"] for g in goals if today_name in (g.get("days") or [])]

        # Streaks
        try:
            streak_res = (
                supabase.table("streaks")
                .select("goal_id, current_streak, longest_streak, last_checkin")
                .eq("user_id", user_id)
                .execute()
            )
            streak_map = {s["goal_id"]: s for s in (streak_res.data or [])}
        except Exception:
            logger.exception(f"[FitnessProvider] streaks fetch failed for {user_id}")
            streak_map = {}

        enriched:       list[dict] = []
        streak_summary: list[dict] = []
        broken_streaks: list[dict] = []

        for g in goals:
            s = streak_map.get(g["id"], {})
            enriched.append({
                "activity":       g["activity"],
                "days":           g.get("days") or [],
                "current_streak": s.get("current_streak", 0),
                "longest_streak": s.get("longest_streak", 0),
                "last_checkin":   s.get("last_checkin"),
            })
            summary = {
                "activity": g["activity"],
                "current":  s.get("current_streak", 0),
                "longest":  s.get("longest_streak", 0),
            }
            streak_summary.append(summary)

            if s.get("current_streak", 0) > 0 and s.get("last_checkin"):
                try:
                    last = datetime.fromisoformat(str(s["last_checkin"])).date()
                    if last < yesterday:
                        broken_streaks.append(summary)
                except Exception:
                    pass

        # Missed-goal detection uses recent check-in logs injected by UserContextProvider
        # via params. Fall back to empty string if not yet available (provider order).
        checkin_text = params.get("recent_checkin_text", "")
        missed_today = [a for a in due_today if a.lower() not in checkin_text]

        # Prompt lines
        if due_today:
            result.prompt_lines.append(f"Goals scheduled today: {', '.join(due_today)}")
        if missed_today:
            result.prompt_lines.append(f"Missed today (no check-in): {', '.join(missed_today)}")
        if streak_summary:
            result.prompt_lines.append("Streaks:")
            for s in streak_summary:
                result.prompt_lines.append(
                    f"  {s['activity']}: {s['current']}-day streak (best: {s['longest']})"
                )
        if broken_streaks:
            broken = [f"{s['activity']} (had {s['current']} days)" for s in broken_streaks]
            result.prompt_lines.append(f"Broken streaks: {', '.join(broken)}")

        # Anomaly scoring
        if missed_today:
            result.anomaly_score += 2
            result.anomaly_reasons.append(
                f"no check-in for scheduled goal(s): {', '.join(missed_today)}"
            )
        if broken_streaks:
            result.anomaly_score += 2
            names = [s["activity"] for s in broken_streaks]
            result.anomaly_reasons.append(f"streak broken: {', '.join(names)}")

        result.metadata = {
            "active_goals":    enriched,
            "goals_due_today": due_today,
            "missed_today":    missed_today,
            "streak_summary":  streak_summary,
            "broken_streaks":  broken_streaks,
        }
        return result


# ---------------------------------------------------------------------------
# UserContextProvider — user_context table (logs, insights, mood, obstacles)
# ---------------------------------------------------------------------------

class UserContextProvider(ContextProvider):
    """
    Reads the user_context table: recent check-ins, journal entries, coach
    insights, mood/energy signals, and any other tagged context entries.

    Also exposes recent check-in text via params so FitnessProvider can do
    missed-goal detection without a second DB round-trip. Because providers
    run concurrently, FitnessProvider uses params.get('recent_checkin_text', '')
    with a graceful fallback — the params dict is pre-populated by
    get_coaching_context() if UserContextProvider is listed first, otherwise
    FitnessProvider degrades safely.
    """

    @property
    def name(self) -> str:
        return "context"

    async def fetch(self, user_id: str, params: dict) -> ProviderResult:
        result = ProviderResult()

        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            res = (
                supabase.table("user_context")
                .select("type, description, created_at")
                .eq("user_id", user_id)
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(25)
                .execute()
            )
            entries = res.data or []
        except Exception:
            logger.exception(f"[UserContextProvider] fetch failed for {user_id}")
            return result

        if not entries:
            return result

        # Group by type for cleaner rendering
        by_type: dict[str, list[str]] = {}
        for e in entries:
            by_type.setdefault(e["type"], []).append(e["description"])

        # Expose check-in text for FitnessProvider cross-reference
        checkin_text = " ".join(d.lower() for d in by_type.get("check-in", []))
        params["recent_checkin_text"] = checkin_text

        # Render prompt lines grouped by type
        type_labels = {
            "check-in":      "Check-ins",
            "personal":      "Journal",
            "coach_insight": "Coach insights",
            "mood":          "Mood",
            "energy":        "Energy",
            "struggle":      "Struggles",
            "win":           "Wins",
            "obstacle":      "Obstacles",
        }
        for entry_type, descriptions in by_type.items():
            label = type_labels.get(entry_type, entry_type.replace("_", " ").title())
            for desc in descriptions:
                result.prompt_lines.append(f"{label}: {desc}")

        # Anomaly: repeated struggle entries or explicit obstacle patterns
        struggles = by_type.get("struggle", [])
        if len(struggles) >= 2:
            result.anomaly_score += 1
            result.anomaly_reasons.append(
                f"user logged {len(struggles)} struggles in the last 24h"
            )

        result.metadata = {
            "entries":       entries,
            "by_type":       by_type,
            "checkin_text":  checkin_text,
        }
        return result


# ---------------------------------------------------------------------------
# ReminderProvider — overdue and upcoming reminders
# ---------------------------------------------------------------------------

class ReminderProvider(ContextProvider):

    @property
    def name(self) -> str:
        return "reminders"

    async def fetch(self, user_id: str, params: dict) -> ProviderResult:
        now_utc = params["now_utc"]
        result  = ProviderResult()

        try:
            six_h = now_utc + timedelta(hours=6)
            res = (
                supabase.table("reminders")
                .select("description, scheduled_for")
                .eq("user_id", user_id)
                .eq("sent", False)
                .lte("scheduled_for", six_h.isoformat())
                .execute()
            )
            rows = res.data or []
        except Exception:
            logger.exception(f"[ReminderProvider] fetch failed for {user_id}")
            return result

        overdue:  list[dict] = []
        upcoming: list[dict] = []

        for r in rows:
            if not r.get("scheduled_for"):
                continue
            scheduled = datetime.fromisoformat(r["scheduled_for"].replace("Z", "+00:00"))
            if scheduled < now_utc:
                overdue.append({"description": r["description"]})
            else:
                hours_away = max(1, int((scheduled - now_utc).total_seconds() / 3600))
                upcoming.append({"description": r["description"], "hours_away": hours_away})

        if overdue:
            items = [r["description"] for r in overdue]
            result.prompt_lines.append(f"Overdue (unsent): {', '.join(items)}")
            result.anomaly_score  += 1
            result.anomaly_reasons.append(f"overdue reminder(s): {', '.join(items)}")

        if upcoming:
            items = [f"{r['description']} (in {r['hours_away']}h)" for r in upcoming]
            result.prompt_lines.append(f"Upcoming (next 6h): {', '.join(items)}")

        result.metadata = {"overdue": overdue, "upcoming": upcoming}
        return result


# ---------------------------------------------------------------------------
# NutritionProvider — nutrition_logs table (strongly typed, reporting_date)
# ---------------------------------------------------------------------------

class NutritionProvider(ContextProvider):
    """
    Reads nutrition_logs for today's reporting_date (user timezone).

    Rule: always filter on reporting_date = <today>, never on created_at.
    This handles the edge case where a user logs dinner after midnight in UTC
    but the entry still belongs to the previous calendar day in their timezone.
    """

    @property
    def name(self) -> str:
        return "nutrition"

    async def fetch(self, user_id: str, params: dict) -> ProviderResult:
        result         = ProviderResult()
        reporting_date = params["reporting_date"]   # date in user's timezone

        try:
            res = (
                supabase.table("nutrition_logs")
                .select("estimated_calories, food_description, reporting_date, created_at")
                .eq("user_id", user_id)
                .eq("reporting_date", reporting_date.isoformat())
                .order("created_at", desc=False)
                .execute()
            )
            rows = res.data or []
        except Exception as exc:
            logger.exception(f"[NutritionProvider] fetch failed for {user_id}")
            return result

        if not rows:
            return result

        total_kcal = sum(r.get("estimated_calories") or 0 for r in rows)
        meals      = [r["food_description"] for r in rows if r.get("food_description")]

        result.prompt_lines.append(f"Logged today ({reporting_date}): {len(rows)} meal(s)")
        result.prompt_lines.append(f"Total estimated calories: {total_kcal} kcal")
        if meals:
            result.prompt_lines.append(f"Foods: {', '.join(meals)}")

        result.metadata = {
            "rows":        rows,
            "total_kcal":  total_kcal,
            "meal_count":  len(rows),
            "reporting_date": reporting_date.isoformat(),
        }
        return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Order matters for params cross-population: UserContextProvider runs first
# so it can expose recent_checkin_text before FitnessProvider reads it.
# NutritionProvider is live — nutrition_logs table exists and is indexed on
# (user_id, reporting_date).

PROVIDERS: list[ContextProvider] = [
    UserContextProvider(),
    FitnessProvider(),
    ReminderProvider(),
    NutritionProvider(),
    # SleepProvider(),      ← add when sleep_logs table is ready
    # MoodTrendProvider(),
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def get_coaching_context(
    user_id: str,
    user_timezone: str = "America/New_York",
) -> CoachingContext:
    """
    Run all registered providers concurrently, merge results, and return a
    CoachingContext ready for prompt injection.
    """
    try:
        tz = pytz.timezone(user_timezone)
    except Exception:
        tz = pytz.UTC

    now_local = datetime.now(tz)

    # Shared params dict — providers may read AND write to coordinate data.
    # reporting_date is the canonical daily grouping key for all structured
    # log tables (nutrition_logs, sleep_logs, etc.) — always in user timezone.
    params: dict = {
        "today_name":     now_local.strftime("%A").lower(),
        "yesterday":      (datetime.now(timezone.utc) - timedelta(days=1)).date(),
        "now_utc":        datetime.now(timezone.utc),
        "reporting_date": now_local.date(),   # date in user's local timezone
    }

    tasks = [
        asyncio.create_task(p.fetch(user_id, params), name=p.name)
        for p in PROVIDERS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ctx = CoachingContext()

    for provider, result in zip(PROVIDERS, results):
        if isinstance(result, Exception):
            logger.error(f"[coaching] provider '{provider.name}' raised: {result}")
            continue
        if result.prompt_lines:
            ctx.provider_blocks[provider.name] = result.prompt_lines
            ctx.provider_labels[provider.name] = provider.prompt_label
        ctx.anomaly_score   += result.anomaly_score
        ctx.anomaly_reasons.extend(result.anomaly_reasons)
        ctx.provider_data[provider.name] = result.metadata

    ctx.has_anomaly = ctx.anomaly_score > 0
    return ctx


# ---------------------------------------------------------------------------
# Feedback Loop — save coach insights back into user_context
# ---------------------------------------------------------------------------

async def save_coach_insight(user_id: str, insight_text: str) -> None:
    """
    Persist a coach-generated insight into user_context (type='coach_insight').

    Called after the user answers a root-cause question so the coach's
    interpretation of the answer is stored for future context injection.
    Entries have no expiry — they persist until manually cleared.
    """
    try:
        supabase.table("user_context").insert({
            "user_id":     user_id,
            "type":        "coach_insight",
            "description": insight_text.strip()[:1000],
            "expires_at":  None,
        }).execute()
        logger.info(f"[coaching] saved coach_insight for {user_id}: {insight_text[:60]}")
    except Exception:
        logger.exception(f"[coaching] save_coach_insight failed for {user_id}")


# ---------------------------------------------------------------------------
# Atomic logging — structured log tables
# ---------------------------------------------------------------------------

async def log_nutrition(
    user_id: str,
    calories: int,
    food_description: str,
    reporting_date: date | None = None,
    image_url: str | None = None,
    gemini_analysis: dict | None = None,
) -> str:
    """
    Insert one row into nutrition_logs.

    Always uses reporting_date (user timezone date) for daily grouping — never
    raw created_at timestamps. Callers must pass the reporting_date computed
    from the user's local timezone; it defaults to today in UTC if omitted.

    Returns: created_at timestamp string (ISO 8601) on success.
    Raises:  DatabaseLoggingError on any DB failure.

    Rule: calorie/macro data must NEVER go into user_context. Use this function
    so that NutritionProvider can aggregate them with typed SQL columns.
    """
    if reporting_date is None:
        reporting_date = datetime.now(timezone.utc).date()

    try:
        res = supabase.table("nutrition_logs").insert({
            "user_id":            user_id,
            "estimated_calories": max(0, int(calories)),
            "food_description":   food_description.strip()[:500],
            "reporting_date":     reporting_date.isoformat(),
            "image_url":          image_url,
            "gemini_analysis":    gemini_analysis or {},
        }).execute()

        created_at: str = res.data[0]["created_at"]
        logger.info(
            f"[coaching] nutrition log saved for {user_id}: "
            f"{calories} kcal, {food_description[:40]}, date={reporting_date}"
        )
        return created_at

    except Exception as exc:
        logger.exception(f"[coaching] log_nutrition failed for {user_id}")
        raise DatabaseLoggingError(
            f"Failed to save nutrition log for user {user_id}: {exc}"
        ) from exc
