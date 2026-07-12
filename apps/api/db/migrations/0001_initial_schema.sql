-- Kairos initial schema
-- Source of truth: ARCHITECTURE.md section 7. Do not diverge without updating both.

-- Users (managed by Supabase Auth, extended here)
CREATE TABLE profiles (
  id UUID REFERENCES auth.users PRIMARY KEY,
  email TEXT NOT NULL,
  full_name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- API Keys
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  key_hash TEXT NOT NULL UNIQUE,      -- store hash, never plaintext
  key_prefix TEXT NOT NULL,           -- show user first 8 chars e.g. "kai_live_xxxx..."
  name TEXT NOT NULL,                 -- user-given label
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT TRUE
);

-- Pipelines (RAG pipelines the user registers)
CREATE TABLE pipelines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  framework TEXT,                     -- 'langchain', 'llamaindex', 'custom'
  created_at TIMESTAMPTZ DEFAULT NOW(),
  is_active BOOLEAN DEFAULT TRUE
);

-- Traces (one row per RAG query)
CREATE TABLE traces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pipeline_id UUID REFERENCES pipelines(id) ON DELETE CASCADE,
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  query TEXT NOT NULL,
  retrieved_chunks JSONB NOT NULL,    -- [{content, score, doc_id, metadata}]
  reranked_chunks JSONB,              -- optional, if reranking used
  final_answer TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  token_count_input INTEGER,
  token_count_output INTEGER,
  estimated_cost_usd DECIMAL(10,6),
  metadata JSONB,                     -- user-provided extra fields
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Eval Scores (computed per trace by eval worker)
CREATE TABLE eval_scores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id UUID REFERENCES traces(id) ON DELETE CASCADE,
  pipeline_id UUID REFERENCES pipelines(id) ON DELETE CASCADE,
  faithfulness DECIMAL(4,3),          -- 0.000 to 1.000
  answer_relevance DECIMAL(4,3),
  context_precision DECIMAL(4,3),
  hallucination_flag BOOLEAN,
  hallucination_detail TEXT,          -- what was hallucinated
  failure_reason TEXT,                -- human-readable root cause
  failure_category TEXT,              -- 'chunking', 'embedding', 'reranking', 'prompt', 'model'
  computed_at TIMESTAMPTZ DEFAULT NOW(),
  model_used TEXT DEFAULT 'claude-haiku-4-5'
);

-- Chunk Index (for chunk utility / graveyard analysis)
CREATE TABLE chunk_index (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pipeline_id UUID REFERENCES pipelines(id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL,             -- user's chunk identifier
  content_preview TEXT,               -- first 200 chars
  doc_source TEXT,                    -- source document name
  retrieval_count INTEGER DEFAULT 0,  -- how many times retrieved
  last_retrieved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(pipeline_id, chunk_id)
);

-- Agent Projects
CREATE TABLE agent_projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  framework TEXT NOT NULL,            -- 'langgraph', 'crewai', 'autogen', 'custom'
  system_prompt TEXT,
  tools JSONB,                        -- [{name, description}]
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Agent Test Runs
CREATE TABLE agent_test_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_project_id UUID REFERENCES agent_projects(id) ON DELETE CASCADE,
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  status TEXT DEFAULT 'pending',      -- 'pending', 'running', 'completed', 'failed'
  total_simulations INTEGER NOT NULL,
  completed_simulations INTEGER DEFAULT 0,
  reliability_score DECIMAL(5,2),     -- 0.00 to 100.00
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Agent Simulation Results (one row per simulated interaction)
CREATE TABLE agent_simulations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  test_run_id UUID REFERENCES agent_test_runs(id) ON DELETE CASCADE,
  scenario_type TEXT NOT NULL,        -- 'normal', 'adversarial', 'edge_case', 'injection'
  scenario_description TEXT NOT NULL,
  input_message TEXT NOT NULL,
  agent_output TEXT,
  tool_calls JSONB,                   -- [{tool_name, input, output, correct}]
  steps_taken INTEGER,
  task_completed BOOLEAN,
  hallucinated BOOLEAN,
  looped BOOLEAN,                     -- did agent get stuck in a loop
  unexpected_action BOOLEAN,
  failure_reason TEXT,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alerts
CREATE TABLE alerts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  pipeline_id UUID REFERENCES pipelines(id),
  agent_project_id UUID REFERENCES agent_projects(id),
  alert_type TEXT NOT NULL,           -- 'faithfulness_drop', 'hallucination_spike', 'reliability_drop'
  severity TEXT NOT NULL,             -- 'warning', 'critical'
  message TEXT NOT NULL,
  metric_before DECIMAL(5,3),
  metric_after DECIMAL(5,3),
  resolved BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usage Tracking (for free tier limits)
CREATE TABLE usage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
  month DATE NOT NULL,                -- first day of month
  traces_count INTEGER DEFAULT 0,
  agent_runs_count INTEGER DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, month)
);

-- Indexes for performance
CREATE INDEX idx_traces_pipeline_id ON traces(pipeline_id);
CREATE INDEX idx_traces_created_at ON traces(created_at DESC);
CREATE INDEX idx_eval_scores_trace_id ON eval_scores(trace_id);
CREATE INDEX idx_eval_scores_pipeline_id ON eval_scores(pipeline_id);
CREATE INDEX idx_eval_scores_computed_at ON eval_scores(computed_at DESC);
CREATE INDEX idx_agent_simulations_test_run ON agent_simulations(test_run_id);
CREATE INDEX idx_chunk_index_pipeline ON chunk_index(pipeline_id);
CREATE INDEX idx_chunk_index_retrieval_count ON chunk_index(retrieval_count);
