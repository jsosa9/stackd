import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// ── Draft onboarding types ────────────────────────────────────────────────────
// Backed by sessionStorage so data survives the OAuth redirect (full page
// navigation) but is scoped to the tab and cleared when it closes.
// Step 7 is the sole Supabase writer; clear() wipes sessionStorage on success.

// Per-day schedule entry stored in activitySchedules (UI state).
export interface DaySchedule {
  count: number;
  times: string[]; // "HH:MM" 24-hour strings, may be empty
}

export interface GoalRow {
  activity: string;
  category: string;
  // Full lowercase day names: "monday", "tuesday", …
  days: string[];
  // Matches goals.times_per_day JSONB column.
  // number when no times set: { monday: 2 }
  // object when times are set: { monday: { count: 2, times: ["08:00","20:00"] } }
  times_per_day: Record<string, number | { count: number; times: string[] }>;
}

export interface QuizData {
  // Step 1a — Selected activities (UI state; converted to stagedGoals on step 1b NEXT)
  selectedActivities?: Record<string, string[]>;

  // Step 1b — Activity schedules (kept for back-navigation; canonical form is stagedGoals)
  activitySchedules?: Record<string, { days: Record<string, DaySchedule> }>;

  // Step 1b → final write — flat, 1 row per activity, matches goals table schema
  stagedGoals?: GoalRow[];

  // Step 2 — Build your coach
  coachName?: string;
  coachSetupMode?: 'quick' | 'custom' | 'celebrity';
  coachPersonality?: 'hype' | 'tough' | 'gentle' | 'funny';
  celebrityName?: string;
  celebrityPersonality?: Record<string, unknown>;
  coachTalkStyle?: string[];
  coachEmojiUsage?: string;
  coachMessageLength?: string;
  coachMissBehavior?: string;
  coachOpenerStyle?: string;
  coachIntensity?: number;
  customCoachSoundsLike?: string;
  customCoachPersonalityDesc?: string;
  customCoachCelebrationStyle?: string;
  customCoachMissedDayResponse?: string;
  customCoachFavoritePhrase?: string;
  customCoachAvoidPhrases?: string;
  customCoachMotivationStyle?: string;
  customCoachTone?: string;
  customCoachSpecialRules?: string;

  // Step 3 — About you
  name?: string;
  age?: string;
  occupation?: 'student' | 'working' | 'both' | 'other';
  obstacles?: string[];
  experience?: 'newbie' | 'tried' | 'consistent' | 'relapse';
  successVision?: string;

  // Step 4 — Boundaries (group → coach_settings.custom_build)
  boundaries?: {
    avoidTopics?:      string[];
    restDayBehavior?:  'silence' | 'light' | 'celebrate' | 'flexible';
    directnessLevel?:  number;
    multiTextAllowed?: boolean;
  };

  // Step 4 — Schedule (matches schedule.checkin_time exactly: "HH:MM" 24h)
  checkinTime?: string;
  timezone?: string;

  // Step 5 — Motivation
  motivationEnabled?: boolean;
  motivationFrequency?: string;
  motivationWindowStart?: string;
  motivationWindowEnd?: string;
  motivationWindowStartAmPm?: string;
  motivationWindowEndAmPm?: string;
  motivationStyles?: string[];
  motivationPullFrom?: string;
  morningKickstartEnabled?: boolean;
  morningKickstartTime?: string;
  morningKickstartAmPm?: string;
  eveningReflectionEnabled?: boolean;
  eveningReflectionTime?: string;
  eveningReflectionAmPm?: string;

  // Step 6 — Phone (→ users.phone / users.phone_verified)
  phone?: string;
  phoneVerified?: boolean;
}

// ── Zustand store ─────────────────────────────────────────────────────────────

interface QuizStore {
  data: QuizData;
  patch: (partial: Partial<QuizData>) => void;
  clear: () => void;
}

export const useQuiz = create<QuizStore>()(
  persist(
    (set) => ({
      data: {},
      patch: (partial) => set((state) => ({ data: { ...state.data, ...partial } })),
      clear: () => set({ data: {} }),
    }),
    {
      name: 'stackd-quiz',
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);
