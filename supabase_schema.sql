-- Run this in the Supabase SQL editor

-- Users table (extends auth.users)
CREATE TABLE IF NOT EXISTS public.users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  phone TEXT,
  name TEXT,
  age INTEGER,
  occupation TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Goals table
CREATE TABLE IF NOT EXISTS public.goals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  activity TEXT NOT NULL,
  category TEXT NOT NULL,
  days TEXT[] DEFAULT '{}',
  times_per_day JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Coach settings table
CREATE TABLE IF NOT EXISTS public.coach_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  coach_name TEXT NOT NULL DEFAULT 'Coach',
  coach_emoji TEXT NOT NULL DEFAULT '💪',
  personality_preset TEXT NOT NULL DEFAULT 'best-friend',
  tone TEXT NOT NULL DEFAULT 'friendly',
  emoji_usage TEXT NOT NULL DEFAULT 'moderate',
  message_length TEXT NOT NULL DEFAULT 'medium',
  miss_behavior TEXT NOT NULL DEFAULT 'gentle',
  checkin_start TEXT NOT NULL DEFAULT 'tomorrow',
  intensity TEXT NOT NULL DEFAULT 'medium',
  custom_build JSONB DEFAULT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id)
);

-- Schedule table
CREATE TABLE IF NOT EXISTS public.schedule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  checkin_time TEXT NOT NULL DEFAULT '08:00',
  timezone TEXT NOT NULL DEFAULT 'America/New_York',
  motivation_enabled BOOLEAN DEFAULT TRUE,
  motivation_frequency TEXT DEFAULT 'Once a day',
  motivation_window_start TEXT DEFAULT '09:00',
  motivation_window_end TEXT DEFAULT '20:00',
  motivation_styles TEXT[] DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id)
);

-- Messages table
CREATE TABLE IF NOT EXISTS public.messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Row Level Security
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.coach_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

-- RLS Policies — users can only read/write their own data
CREATE POLICY "Users can manage own profile" ON public.users
  FOR ALL USING (auth.uid() = id);

CREATE POLICY "Users can manage own goals" ON public.goals
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users can manage own coach settings" ON public.coach_settings
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users can manage own schedule" ON public.schedule
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users can view own messages" ON public.messages
  FOR SELECT USING (auth.uid() = user_id);

-- Service role can insert messages (for backend)
CREATE POLICY "Service role can insert messages" ON public.messages
  FOR INSERT WITH CHECK (true);

-- Enable real-time for messages
ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
