import { supabase } from '@/lib/supabase';
import type { QuizData } from '@/lib/quiz-state';

// ── Private helpers ───────────────────────────────────────────────────────────

function convertTo24h(hour: number, minute: number, ampm: 'AM' | 'PM'): string {
  let h = hour;
  if (ampm === 'PM' && h !== 12) h += 12;
  if (ampm === 'AM' && h === 12) h = 0;
  return `${String(h).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

function buildCustomBuild(quiz: QuizData): Record<string, unknown> {
  return {
    // Step 2: top-level coach config (stored here because setup_type, tone,
    // emoji_usage, message_length, intensity, sounds_like columns are not yet
    // in the live DB — move to top-level columns once migration runs)
    setup_type:    quiz.coachSetupMode    ?? 'quick',
    tone:          quiz.coachTalkStyle?.join(', ') ?? null,
    talk_style:    quiz.coachTalkStyle    ?? [],
    emoji_usage:   quiz.coachEmojiUsage   ?? null,
    message_length: quiz.coachMessageLength ?? null,
    miss_behavior: quiz.coachMissBehavior ?? null,
    opener_style:  quiz.coachOpenerStyle  ?? null,
    intensity:     quiz.coachIntensity    ?? null,

    // Step 2: custom-mode freeform fields
    sounds_like:          quiz.coachSetupMode === 'custom' ? (quiz.customCoachSoundsLike ?? null) : null,
    personality_desc:     quiz.customCoachPersonalityDesc    ?? null,
    celebration_style:    quiz.customCoachCelebrationStyle   ?? null,
    missed_day_response:  quiz.customCoachMissedDayResponse  ?? null,
    favorite_phrase:      quiz.customCoachFavoritePhrase     ?? null,
    avoid_phrases:        quiz.customCoachAvoidPhrases       ?? null,
    motivation_style:     quiz.customCoachMotivationStyle    ?? null,
    custom_tone:          quiz.customCoachTone               ?? null,
    special_rules:        quiz.customCoachSpecialRules       ?? null,

    // Step 3: user context the coach should know
    obstacles:      quiz.obstacles     ?? [],
    experience:     quiz.experience    ?? null,
    success_vision: quiz.successVision ?? null,

    // Step 4: boundaries
    avoid_topics:       quiz.boundaries?.avoidTopics     ?? [],
    rest_day_behavior:  quiz.boundaries?.restDayBehavior ?? null,
    directness_level:   quiz.boundaries?.directnessLevel ?? 3,
    multi_text_allowed: quiz.boundaries?.multiTextAllowed ?? true,
  };
}

// ── Commit pipeline ───────────────────────────────────────────────────────────

/**
 * The single deterministic onboarding commit. Safe to call multiple times
 * with the same data — all four writes are idempotent:
 *   users          → upsert on id
 *   goals          → delete-by-user + insert  (no duplicates on retry)
 *   schedule       → upsert on user_id
 *   coach_settings → upsert on user_id
 *
 * User identity is resolved here via supabase.auth.getUser() — the only
 * server-verified call. No userId is accepted as a parameter so there is
 * no path for a caller to inject a spoofed identity.
 *
 * Step 7 is the only intended caller. Throws on the first failure.
 */
export async function finalizeOnboarding(quiz: QuizData): Promise<void> {
  // getUser() makes a server round-trip — safe against tampered JWT payloads.
  const { data: { user }, error: authErr } = await supabase.auth.getUser();
  if (authErr || !user) throw new Error('Not authenticated');

  const userId = user.id;
  const email  = user.email ?? '';

  // 1. USERS ── upsert ────────────────────────────────────────────────────────
  const { error: userErr } = await supabase
    .from('users')
    .upsert(
      {
        id:         userId,
        email,
        phone:      quiz.phone      ?? null,
        name:       quiz.name       ?? null,
        age:        quiz.age != null ? parseInt(quiz.age, 10) : null,
        occupation: quiz.occupation ?? null,
        // phone_verified omitted — column not yet in the live DB.
        // Add it back once the migration runs: ALTER TABLE users ADD COLUMN phone_verified BOOLEAN DEFAULT FALSE;
      },
      { onConflict: 'id' },
    );
  if (userErr) throw new Error(`users: ${userErr.message}`);

  // 2. GOALS ── delete + insert (idempotent; no orphan rows on retry) ─────────
  const { error: delErr } = await supabase
    .from('goals')
    .delete()
    .eq('user_id', userId);
  if (delErr) throw new Error(`goals delete: ${delErr.message}`);

  const goalRows = (quiz.stagedGoals ?? []).map(g => ({ ...g, user_id: userId }));
  if (goalRows.length > 0) {
    const { error: insErr } = await supabase.from('goals').insert(goalRows);
    if (insErr) throw new Error(`goals insert: ${insErr.message}`);
  }

  // 3. SCHEDULE ── upsert ─────────────────────────────────────────────────────
  // Only sending the columns that exist in the live DB.
  // motivation_*, morning_kickstart_*, evening_reflection_* are defined in the
  // schema SQL but were not in the initial migration — add them back once you
  // run: ALTER TABLE schedule ADD COLUMN motivation_enabled BOOLEAN DEFAULT FALSE, ...
  const { error: schedErr } = await supabase
    .from('schedule')
    .upsert(
      {
        user_id:      userId,
        checkin_time: quiz.checkinTime ?? '08:00',
        timezone:     quiz.timezone    ?? 'America/New_York',
      },
      { onConflict: 'user_id' },
    );
  if (schedErr) throw new Error(`schedule: ${schedErr.message}`);

  // 4. COACH SETTINGS ── upsert ───────────────────────────────────────────────
  // Only columns confirmed in the live DB. setup_type, tone, emoji_usage,
  // message_length, intensity, sounds_like are in the schema SQL but not yet
  // migrated — they're stored inside custom_build (JSONB) for now.
  const { error: coachErr } = await supabase
    .from('coach_settings')
    .upsert(
      {
        user_id:            userId,
        coach_name:         quiz.coachName       ?? 'Coach',
        personality_preset: quiz.coachPersonality ?? null,
        custom_build:       buildCustomBuild(quiz),
      },
      { onConflict: 'user_id' },
    );
  if (coachErr) throw new Error(`coach_settings: ${coachErr.message}`);
}

// ── Post-onboarding validation ────────────────────────────────────────────────

/**
 * Light read-back check after finalizeOnboarding.
 * Confirms the three critical rows are present for the authenticated user.
 * Throws a descriptive error on any missing row so the caller can surface it.
 */
export async function validateOnboarding(): Promise<void> {
  const { data: { user }, error: authErr } = await supabase.auth.getUser();
  if (authErr || !user) throw new Error('Not authenticated');

  const uid = user.id;

  const [
    { count: userCount,  error: userErr  },
    { count: goalCount,  error: goalErr  },
    { count: schedCount, error: schedErr },
  ] = await Promise.all([
    supabase.from('users')   .select('*', { count: 'exact', head: true }).eq('id',      uid),
    supabase.from('goals')   .select('*', { count: 'exact', head: true }).eq('user_id', uid),
    supabase.from('schedule').select('*', { count: 'exact', head: true }).eq('user_id', uid),
  ]);

  if (userErr)  throw new Error(`validate/users: ${userErr.message}`);
  if (goalErr)  throw new Error(`validate/goals: ${goalErr.message}`);
  if (schedErr) throw new Error(`validate/schedule: ${schedErr.message}`);

  if (!userCount)  throw new Error('Setup incomplete: user record missing.');
  if (!goalCount)  throw new Error('Setup incomplete: no goals were saved.');
  if (!schedCount) throw new Error('Setup incomplete: schedule record missing.');
}
