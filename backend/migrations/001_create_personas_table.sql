-- Personas table for caching research results
-- Run this in the Supabase SQL editor

CREATE TABLE IF NOT EXISTS public.personas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  youtube_transcripts TEXT,
  perplexity_research TEXT,
  synthesized_profile TEXT,
  request_count INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE public.personas ENABLE ROW LEVEL SECURITY;

-- Allow service role to read/write (for backend research pipeline)
CREATE POLICY "Service role can manage personas" ON public.personas
  FOR ALL USING (true);

-- Optional: Add index on name for faster lookups
CREATE INDEX IF NOT EXISTS personas_name_idx ON public.personas(name);

-- Optional: Add index on request_count to find popular personas
CREATE INDEX IF NOT EXISTS personas_request_count_idx ON public.personas(request_count DESC);
