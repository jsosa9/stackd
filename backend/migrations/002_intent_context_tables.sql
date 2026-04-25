-- Intent detection and context awareness tables
-- Run this in the Supabase SQL editor

-- Reminders: one-time scheduled messages
CREATE TABLE IF NOT EXISTS public.reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL,
    reminder_message TEXT NOT NULL,
    sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Deadlines: recurring check-ins leading up to a date
CREATE TABLE IF NOT EXISTS public.deadlines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    deadline_date DATE NOT NULL,
    daily_checkin BOOLEAN DEFAULT TRUE,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User context: temporary situational awareness
CREATE TABLE IF NOT EXISTS public.user_context (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    type TEXT NOT NULL, -- struggle/win/personal/travel/health/mood/energy/social
    description TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Habit patterns: detected behavioral patterns over time
CREATE TABLE IF NOT EXISTS public.habit_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    pattern_type TEXT NOT NULL, -- quiet_day/crash_time/strong_day/weak_day
    description TEXT NOT NULL,
    day_of_week INTEGER, -- 0-6 (Sun-Sat), null if time based
    time_of_day TIME, -- null if day based
    confidence INTEGER DEFAULT 1, -- increases each time pattern is confirmed
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Streaks: tracking consecutive goal completion
CREATE TABLE IF NOT EXISTS public.streaks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    goal_id UUID REFERENCES public.goals(id) ON DELETE CASCADE,
    current_streak INTEGER DEFAULT 0,
    longest_streak INTEGER DEFAULT 0,
    last_checkin DATE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Social bets: accountability to others
CREATE TABLE IF NOT EXISTS public.social_bets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    target TEXT NOT NULL,
    deadline DATE,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Enable RLS on all tables
ALTER TABLE public.reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deadlines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.habit_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.streaks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.social_bets ENABLE ROW LEVEL SECURITY;

-- Allow service role full access to all tables
CREATE POLICY "Service role can manage reminders" ON public.reminders FOR ALL USING (true);
CREATE POLICY "Service role can manage deadlines" ON public.deadlines FOR ALL USING (true);
CREATE POLICY "Service role can manage user_context" ON public.user_context FOR ALL USING (true);
CREATE POLICY "Service role can manage habit_patterns" ON public.habit_patterns FOR ALL USING (true);
CREATE POLICY "Service role can manage streaks" ON public.streaks FOR ALL USING (true);
CREATE POLICY "Service role can manage social_bets" ON public.social_bets FOR ALL USING (true);

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS reminders_user_id_idx ON public.reminders(user_id);
CREATE INDEX IF NOT EXISTS reminders_scheduled_for_idx ON public.reminders(scheduled_for);
CREATE INDEX IF NOT EXISTS reminders_sent_idx ON public.reminders(sent);

CREATE INDEX IF NOT EXISTS deadlines_user_id_idx ON public.deadlines(user_id);
CREATE INDEX IF NOT EXISTS deadlines_active_idx ON public.deadlines(active);
CREATE INDEX IF NOT EXISTS deadlines_deadline_date_idx ON public.deadlines(deadline_date);

CREATE INDEX IF NOT EXISTS user_context_user_id_idx ON public.user_context(user_id);
CREATE INDEX IF NOT EXISTS user_context_expires_at_idx ON public.user_context(expires_at);
CREATE INDEX IF NOT EXISTS user_context_type_idx ON public.user_context(type);

CREATE INDEX IF NOT EXISTS habit_patterns_user_id_idx ON public.habit_patterns(user_id);
CREATE INDEX IF NOT EXISTS habit_patterns_day_of_week_idx ON public.habit_patterns(day_of_week);
CREATE INDEX IF NOT EXISTS habit_patterns_confidence_idx ON public.habit_patterns(confidence);

CREATE INDEX IF NOT EXISTS streaks_user_id_idx ON public.streaks(user_id);
CREATE INDEX IF NOT EXISTS streaks_goal_id_idx ON public.streaks(goal_id);

CREATE INDEX IF NOT EXISTS social_bets_user_id_idx ON public.social_bets(user_id);
CREATE INDEX IF NOT EXISTS social_bets_deadline_idx ON public.social_bets(deadline);
