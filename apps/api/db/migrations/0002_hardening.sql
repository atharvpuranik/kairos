-- Kairos hardening migration (2026-07 architecture review, critical + important tier)
-- Run AFTER 0001_initial_schema.sql. Companion cron jobs live in 0003_cron_jobs.sql.

-- ---------------------------------------------------------------------------
-- 1. Auto-create a profiles row on Supabase Auth signup
--    (without this, every FK to profiles breaks for self-served signups)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name)
  VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data->>'full_name')
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ---------------------------------------------------------------------------
-- 2. Eval worker: status-driven queue instead of diff-the-tables polling
-- ---------------------------------------------------------------------------
ALTER TABLE traces ADD COLUMN IF NOT EXISTS eval_status TEXT NOT NULL DEFAULT 'pending';
  -- 'pending' | 'completed' | 'failed' | 'skipped' (sampled out or over daily cap)
ALTER TABLE traces ADD COLUMN IF NOT EXISTS eval_attempts INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_traces_eval_pending
  ON traces (created_at) WHERE eval_status = 'pending';

-- one eval per trace, enforced at the DB so worker races can't double-score
ALTER TABLE eval_scores ADD CONSTRAINT eval_scores_trace_id_unique UNIQUE (trace_id);

-- score comparability across eval-prompt revisions
ALTER TABLE eval_scores ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT 'v1';

-- per-pipeline eval sampling (1.00 = eval every trace)
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS eval_sample_rate NUMERIC(3,2) NOT NULL DEFAULT 1.00
  CHECK (eval_sample_rate >= 0 AND eval_sample_rate <= 1);

-- ---------------------------------------------------------------------------
-- 3. Nightly pipeline health snapshots (week-over-week alerting needs history)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_health_daily (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pipeline_id UUID REFERENCES pipelines(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  health_score DECIMAL(5,2),          -- ((f*0.35)+(r*0.35)+(p*0.30))*100
  avg_faithfulness DECIMAL(4,3),
  avg_answer_relevance DECIMAL(4,3),
  avg_context_precision DECIMAL(4,3),
  eval_count INTEGER NOT NULL DEFAULT 0,
  hallucination_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (pipeline_id, date)
);
CREATE INDEX IF NOT EXISTS idx_health_daily_pipeline_date
  ON pipeline_health_daily (pipeline_id, date DESC);

-- ---------------------------------------------------------------------------
-- 4. Atomic helpers (called by the API / worker via RPC)
-- ---------------------------------------------------------------------------

-- Free-tier quota: atomically consume p_count traces from the user's monthly
-- allowance. Returns true if within limit (and counted), false if it would
-- exceed p_limit (nothing counted).
CREATE OR REPLACE FUNCTION public.consume_trace_quota(
  p_user_id UUID, p_count INTEGER, p_limit INTEGER
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_new INTEGER;
BEGIN
  INSERT INTO usage (user_id, month, traces_count)
  VALUES (p_user_id, date_trunc('month', now())::date, p_count)
  ON CONFLICT (user_id, month) DO UPDATE
    SET traces_count = usage.traces_count + p_count,
        updated_at = now()
    WHERE usage.traces_count + p_count <= p_limit
  RETURNING traces_count INTO v_new;
  RETURN v_new IS NOT NULL;
END;
$$;

-- Atomic chunk retrieval-count upsert (replaces the worker's read-then-write)
CREATE OR REPLACE FUNCTION public.upsert_chunk_retrieval(
  p_pipeline_id UUID, p_chunk_id TEXT, p_preview TEXT, p_source TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO chunk_index (pipeline_id, chunk_id, content_preview, doc_source, retrieval_count, last_retrieved_at)
  VALUES (p_pipeline_id, p_chunk_id, p_preview, p_source, 1, now())
  ON CONFLICT (pipeline_id, chunk_id) DO UPDATE
    SET retrieval_count = chunk_index.retrieval_count + 1,
        last_retrieved_at = now();
END;
$$;

-- Keep RPCs server-side only (service role bypasses RLS/grants anyway)
REVOKE EXECUTE ON FUNCTION public.consume_trace_quota(UUID, INTEGER, INTEGER) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.upsert_chunk_retrieval(UUID, TEXT, TEXT, TEXT) FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. Row Level Security
--    Service-role key (the API + worker) bypasses RLS. These policies exist for
--    the dashboard, which reads Supabase directly with the user's JWT.
--    Writes to traces/eval_scores/chunk_index/usage happen only via the API.
-- ---------------------------------------------------------------------------
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipelines ENABLE ROW LEVEL SECURITY;
ALTER TABLE traces ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_test_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_simulations ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_health_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own profile read" ON profiles
  FOR SELECT TO authenticated USING (id = auth.uid());
CREATE POLICY "own profile update" ON profiles
  FOR UPDATE TO authenticated USING (id = auth.uid()) WITH CHECK (id = auth.uid());

-- key metadata only (hash column is never exposed to clients by policy design:
-- dashboard selects explicit columns; creation/revocation go through the API)
CREATE POLICY "own keys read" ON api_keys
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "own pipelines all" ON pipelines
  FOR ALL TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "own traces read" ON traces
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "own eval scores read" ON eval_scores
  FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM pipelines p WHERE p.id = eval_scores.pipeline_id AND p.user_id = auth.uid())
  );

CREATE POLICY "own chunk index read" ON chunk_index
  FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM pipelines p WHERE p.id = chunk_index.pipeline_id AND p.user_id = auth.uid())
  );

CREATE POLICY "own agent projects all" ON agent_projects
  FOR ALL TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "own test runs read" ON agent_test_runs
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "own simulations read" ON agent_simulations
  FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM agent_test_runs r WHERE r.id = agent_simulations.test_run_id AND r.user_id = auth.uid())
  );

CREATE POLICY "own alerts read" ON alerts
  FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY "own alerts resolve" ON alerts
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "own usage read" ON usage
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "own health read" ON pipeline_health_daily
  FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM pipelines p WHERE p.id = pipeline_health_daily.pipeline_id AND p.user_id = auth.uid())
  );
