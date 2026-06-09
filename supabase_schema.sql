

-- =========================
-- USERS TABLE
-- =========================
CREATE TABLE public.users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  phone TEXT,
  name TEXT,
  age INTEGER,
  occupation TEXT,
  paused BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- GOALS TABLE
-- =========================
CREATE TABLE public.goals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  activity TEXT NOT NULL,
  category TEXT NOT NULL,
  days TEXT[] DEFAULT '{}',
  times_per_day JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- COACH SETTINGS
-- =========================
CREATE TABLE public.coach_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

  -- Basic
  coach_name TEXT NOT NULL DEFAULT 'Coach',
  coach_emoji TEXT NOT NULL DEFAULT '💪',
  personality_preset TEXT NOT NULL DEFAULT 'best-friend',
  tone TEXT NOT NULL DEFAULT 'friendly',
  emoji_usage TEXT NOT NULL DEFAULT 'moderate',
  message_length TEXT NOT NULL DEFAULT 'medium',
  miss_behavior TEXT NOT NULL DEFAULT 'gentle',
  intensity INTEGER DEFAULT 3,

  -- Persona
  coach_setup_type TEXT DEFAULT 'celebrity', -- 'celebrity' | 'custom'
  sounds_like TEXT, -- e.g. "David Goggins"
  custom_build JSONB DEFAULT '{}'::jsonb,

  -- Custom build fields
  custom_coach_sounds_like TEXT,
  custom_coach_personality_desc TEXT,
  custom_coach_tone TEXT[],
  custom_coach_avoid_phrases TEXT,
  custom_coach_favorite_phrase TEXT,
  custom_coach_missed_day_response TEXT,
  custom_coach_celebration_style TEXT,
  custom_coach_special_rules TEXT,

  -- Generated
  generated_system_prompt TEXT,
  persona_research TEXT,

  -- Versioning
  personality_id TEXT,
  version INTEGER DEFAULT 1,
  is_active BOOLEAN DEFAULT TRUE,

  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(user_id)
);

-- =========================
-- SCHEDULE
-- =========================
CREATE TABLE public.schedule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

  checkin_time TEXT NOT NULL DEFAULT '08:00',
  timezone TEXT NOT NULL DEFAULT 'America/New_York',

  motivation_enabled BOOLEAN DEFAULT TRUE,
  motivation_frequency TEXT DEFAULT 'Once a day',
  motivation_window_start TEXT DEFAULT '09:00',
  motivation_window_end TEXT DEFAULT '20:00',
  motivation_styles TEXT[] DEFAULT '{}',
  motivation_from TEXT,

  morning_kickstart BOOLEAN DEFAULT FALSE,
  morning_kickstart_time TEXT,
  evening_reflection BOOLEAN DEFAULT FALSE,
  evening_reflection_time TEXT,

  created_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(user_id)
);

-- =========================
-- MESSAGES
-- =========================
CREATE TABLE public.messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- PHONE LINK TOKENS
-- =========================
CREATE TABLE public.phone_link_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  token TEXT UNIQUE NOT NULL,
  used BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

-- =========================
-- REMINDERS
-- =========================
CREATE TABLE public.reminders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  description TEXT,
  scheduled_for TIMESTAMPTZ,
  reminder_message TEXT,
  sent BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- DEADLINES
-- =========================
CREATE TABLE public.deadlines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  description TEXT,
  deadline_date DATE,
  daily_checkin BOOLEAN DEFAULT TRUE,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- USER CONTEXT
-- =========================
CREATE TABLE public.user_context (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  type TEXT,
  description TEXT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- HABIT PATTERNS
-- =========================
CREATE TABLE public.habit_patterns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  pattern_type TEXT,
  description TEXT,
  day_of_week INTEGER,
  time_of_day TIME,
  confidence INTEGER DEFAULT 1,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- STREAKS
-- =========================
CREATE TABLE public.streaks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  goal_id UUID REFERENCES public.goals(id) ON DELETE CASCADE,
  current_streak INTEGER DEFAULT 0,
  longest_streak INTEGER DEFAULT 0,
  last_checkin DATE,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- SOCIAL BETS
-- =========================
CREATE TABLE public.social_bets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  description TEXT,
  target TEXT,
  deadline DATE,
  completed BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- SENT QUOTES
-- =========================
CREATE TABLE public.sent_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  quote_id TEXT,
  sent_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- PERSONAS (shared persona profiles)
-- =========================
CREATE TABLE public.personas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  personality_id TEXT UNIQUE NOT NULL,
  name TEXT UNIQUE NOT NULL,
  system_instruction TEXT,
  few_shot_examples JSONB DEFAULT '[]'::jsonb,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =========================
-- RLS
-- =========================
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.coach_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phone_link_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deadlines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.habit_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.streaks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.social_bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sent_quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personas ENABLE ROW LEVEL SECURITY;

-- USERS
CREATE POLICY "Users can manage own profile"
ON public.users FOR ALL
USING (auth.uid() = id);

-- GOALS
CREATE POLICY "Users can manage own goals"
ON public.goals FOR ALL
USING (auth.uid() = user_id);

-- COACH SETTINGS
CREATE POLICY "Users can manage own coach settings"
ON public.coach_settings FOR ALL
USING (auth.uid() = user_id);

-- SCHEDULE
CREATE POLICY "Users can manage own schedule"
ON public.schedule FOR ALL
USING (auth.uid() = user_id);

-- MESSAGES
CREATE POLICY "Users can view own messages"
ON public.messages FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Service role can insert messages"
ON public.messages FOR INSERT
WITH CHECK (true);

-- PHONE LINK TOKENS
CREATE POLICY "Users can view own tokens"
ON public.phone_link_tokens FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage tokens"
ON public.phone_link_tokens FOR ALL
WITH CHECK (true);

-- REMINDERS
CREATE POLICY "Users can manage own reminders"
ON public.reminders FOR ALL
USING (auth.uid() = user_id);

-- DEADLINES
CREATE POLICY "Users can manage own deadlines"
ON public.deadlines FOR ALL
USING (auth.uid() = user_id);

-- USER CONTEXT
CREATE POLICY "Users can manage own context"
ON public.user_context FOR ALL
USING (auth.uid() = user_id);

-- HABIT PATTERNS
CREATE POLICY "Users can manage own patterns"
ON public.habit_patterns FOR ALL
USING (auth.uid() = user_id);

-- STREAKS
CREATE POLICY "Users can manage own streaks"
ON public.streaks FOR ALL
USING (auth.uid() = user_id);

-- SOCIAL BETS
CREATE POLICY "Users can manage own bets"
ON public.social_bets FOR ALL
USING (auth.uid() = user_id);

-- SENT QUOTES
CREATE POLICY "Users can manage own sent quotes"
ON public.sent_quotes FOR ALL
USING (auth.uid() = user_id);

-- PERSONAS (readable by all authenticated users, service role manages)
CREATE POLICY "Authenticated users can read personas"
ON public.personas FOR SELECT
USING (auth.role() = 'authenticated');

CREATE POLICY "Service role can manage personas"
ON public.personas FOR ALL
WITH CHECK (true);

-- =========================
-- REALTIME
-- =========================
ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;

-- =========================
-- INDEXES (performance)
-- =========================
CREATE INDEX idx_goals_user_id ON public.goals(user_id);
CREATE INDEX idx_messages_user_id_created ON public.messages(user_id, created_at DESC);
CREATE INDEX idx_reminders_user_scheduled ON public.reminders(user_id, scheduled_for) WHERE sent = FALSE;
CREATE INDEX idx_user_context_user_expires ON public.user_context(user_id, expires_at);
CREATE INDEX idx_streaks_user_goal ON public.streaks(user_id, goal_id);
CREATE INDEX idx_phone_link_tokens_token ON public.phone_link_tokens(token) WHERE used = FALSE;
CREATE INDEX idx_personas_name ON public.personas(name);


-- Drop the unique constraint
ALTER TABLE public.coach_settings DROP CONSTRAINT coach_settings_user_id_key;

-- Set existing rows to a generated personality_id so they aren't null
UPDATE public.coach_settings 
SET personality_id = UPPER(
  SUBSTRING(MD5(RANDOM()::TEXT), 1, 4) || 
  LPAD(FLOOR(RANDOM() * 10000)::TEXT, 4, '0')
)
WHERE personality_id IS NULL;

-- Add indexes
CREATE INDEX idx_coach_settings_personality_id ON public.coach_settings(personality_id);
CREATE INDEX idx_coach_settings_user_active ON public.coach_settings(user_id, is_active);

DROP TABLE public.personas;

CREATE TABLE public.personas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  personality_id TEXT UNIQUE NOT NULL,
  name TEXT UNIQUE NOT NULL,
  system_instruction TEXT,
  few_shot_examples JSONB DEFAULT '[]'::jsonb,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_personas_personality_id ON public.personas(personality_id);

-- Add an index for quick retrieval of specific context types for the Coaching Service
CREATE INDEX idx_user_context_type ON public.user_context(user_id, type);

-- Add this to support vision-based logging
ALTER TABLE public.user_context ADD COLUMN image_url TEXT;
ALTER TABLE public.user_context ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb; 
-- Metadata will store { "calories": 450, "food_type": "Chicken Salad", "confidence": 0.95 }

CREATE TABLE public.nutrition_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  image_url TEXT,
  estimated_calories INTEGER,
  food_description TEXT,
  gemini_analysis JSONB DEFAULT '{}'::jsonb,
  reporting_date DATE DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE public.nutrition_logs ENABLE ROW LEVEL SECURITY;

-- Create policy for the user
CREATE POLICY "Users can manage own nutrition"
ON public.nutrition_logs FOR ALL
USING (auth.uid() = user_id);

-- Index for fast retrieval by date
CREATE INDEX idx_nutrition_logs_user_date ON public.nutrition_logs(user_id, reporting_date);

ALTER TABLE public.users 
  ALTER COLUMN id SET DEFAULT gen_random_uuid(),
  DROP CONSTRAINT users_id_fkey;

ALTER TABLE public.users ADD COLUMN trial_started_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN trial_ends_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN is_paid BOOLEAN DEFAULT FALSE;
ALTER TABLE public.users ADD COLUMN onboarding_step INTEGER DEFAULT 0;
ALTER TABLE public.users ADD COLUMN stripe_customer_id TEXT;

-- TCPA compliance: consent recording (required before any outbound SMS is sent)
-- sms_consent_method values: 'stk_token' | 'quiz_onboarding' | 'web_form'
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS sms_consent_given_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS sms_consent_method TEXT;

-- FIX 1: Create activity_notifications table
CREATE TABLE public.activity_notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  activity TEXT NOT NULL,
  scheduled_date DATE NOT NULL,
  scheduled_time TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'SCHEDULED' 
    CHECK (state IN ('SCHEDULED','NOTIFIED','CONFIRMED',
                     'DECLINED','RESCHEDULED','MISSED')),
  notified_at TIMESTAMPTZ,
  replied_at TIMESTAMPTZ,
  reply_text TEXT,
  rescheduled_to TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.activity_notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own notifications"
ON public.activity_notifications FOR ALL
USING (auth.uid() = user_id);

CREATE INDEX idx_activity_notifications_user_state 
ON public.activity_notifications(user_id, state);

-- FIX 2: Add missing coach_settings columns
ALTER TABLE public.coach_settings 
  ADD COLUMN IF NOT EXISTS coach_personality TEXT DEFAULT 'balanced',
  ADD COLUMN IF NOT EXISTS coach_intensity INTEGER DEFAULT 3,
  ADD COLUMN IF NOT EXISTS coach_talk_style TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS coach_emoji_usage TEXT DEFAULT 'moderate',
  ADD COLUMN IF NOT EXISTS coach_message_length TEXT DEFAULT 'medium',
  ADD COLUMN IF NOT EXISTS coach_miss_behavior TEXT DEFAULT 'compassionate',
  ADD COLUMN IF NOT EXISTS custom_coach_nuclear_option TEXT;

-- Stripe billing columns
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'trial',
  ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_subscription_status
  ON public.users(subscription_status);

-- =========================
-- GOAL COMPLETIONS
-- Tracks daily goal check-ins. Used by nightly summaries and streak logic.
-- The unique constraint prevents double-counting if the user checks in twice.
-- =========================
CREATE TABLE public.goal_completions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  goal_id UUID NOT NULL REFERENCES public.goals(id) ON DELETE CASCADE,
  completed_date DATE NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, goal_id, completed_date)
);

ALTER TABLE public.goal_completions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own completions"
ON public.goal_completions FOR ALL
USING (auth.uid() = user_id);

-- Service role needs INSERT for the backend pipeline
CREATE POLICY "Service role can insert completions"
ON public.goal_completions FOR INSERT
WITH CHECK (true);

CREATE INDEX idx_goal_completions_user_date
ON public.goal_completions(user_id, completed_date DESC);

-- =========================
-- USER MEMORY
-- Rolling memory document per user. Updated nightly by the memory builder job.
-- Stores accumulated coach knowledge: personality signals, obstacles, wins,
-- relationship notes, and coach calibration tips derived from message history.
-- =========================
CREATE TABLE public.user_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  memory_doc JSONB DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id)
);

ALTER TABLE public.user_memory ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own memory"
ON public.user_memory FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage memory"
ON public.user_memory FOR ALL
WITH CHECK (true);

CREATE INDEX idx_user_memory_user_id ON public.user_memory(user_id);

-- =========================
-- TOPIC MEMORY
-- Structured per-topic memory extracted from every inbound message.
-- Each row is one topic, project, event, goal, or concern for a user.
-- status: active | resolved | dormant
-- importance: 0.1 (passing mention) → 1.0 (life-changing event)
-- =========================
CREATE TABLE public.topic_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  topic TEXT NOT NULL,
  summary TEXT NOT NULL,
  importance FLOAT NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'active',
  last_mentioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.topic_memory ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own topic memory"
ON public.topic_memory FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage topic memory"
ON public.topic_memory FOR ALL
WITH CHECK (true);

CREATE INDEX idx_topic_memory_user_status ON public.topic_memory(user_id, status);
CREATE INDEX idx_topic_memory_user_importance ON public.topic_memory(user_id, importance DESC, last_mentioned_at DESC);