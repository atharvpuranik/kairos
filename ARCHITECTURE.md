# Kairos — Architecture Document
**RAG Observability + Agent Reliability Testing Platform**
*Open Source | Built for Indie Devs and Small AI Teams*

---

## 1. Product Vision

Kairos is an open source observability and reliability platform for production AI systems. It solves two problems that every team shipping RAG pipelines and AI agents faces but has no good tool for:

1. **Post-deployment:** You don't know if your RAG pipeline is working, why it degraded, or which documents are causing failures. Existing tools (LangSmith, Arize) either only log traces without reasoning about them, or are priced for enterprises.

2. **Pre-deployment:** You can't systematically stress-test an AI agent before shipping it. Traditional QA tools don't understand non-deterministic agentic systems. You ship and hope.

Kairos closes both gaps in one platform with a 3-line SDK integration, a clean dashboard, and near-zero infrastructure cost.

**Tagline:** "Ship AI pipelines with confidence. Test before you deploy. Monitor after you ship."

---

## 2. Target Users

**Primary:**
- Indie developers building RAG applications
- Small AI teams (2–5 engineers) at startups

**Secondary (Phase 3+):**
- Mid-size engineering teams needing enterprise features

**What they have in common:**
- Technical, move fast, cost-sensitive
- Hate enterprise sales friction and complex setup
- Trust open source tools they can read and self-host
- Active in communities: LangChain Discord, LlamaIndex Discord, r/LocalLLaMA, Hugging Face forums

---

## 3. Go-To-Market Strategy

**Phase 1 (Launch):** Free tier only. Build community. No paid plans.
**Phase 2 (Traction):** Free tier + paid tier once name has momentum.
**Phase 3 (Scale):** Enterprise self-host license + SaaS Pro plan.

**Community channels:**
- GitHub (public repo from day 1, good README, demo GIF)
- LangChain Discord — #tools-and-libraries
- LlamaIndex Discord
- r/MachineLearning, r/LocalLLaMA
- Hugging Face forums
- LinkedIn + Twitter (weekly build-in-public posts)
- Hashnode/dev.to technical blog posts

**Free tier limits:**
- 10,000 RAG queries traced per month
- 500 agent simulation runs per month
- 30-day trace history
- 1 pipeline / 1 agent project
- Community support only

---

## 4. Core Principles

1. **3-line integration.** If setup takes more than 3 lines of code the product fails.
2. **Non-blocking SDK.** The SDK must never slow the user's pipeline. All tracing is async.
3. **Near-zero cost.** Every infrastructure choice uses free tiers until there are paying users.
4. **Self-hostable.** Docker Compose file ships with the repo. Developers trust what they can run locally.
5. **Eval quality first.** If the platform gives wrong scores it loses trust permanently. Quality gates before launch.
6. **Open source core.** Transparent, community-driven, no black-box magic.

---

## 5. Tech Stack

### Frontend
| Layer | Choice | Reason |
|---|---|---|
| Framework | Next.js 14 (App Router) | Best DX, Vercel native, SSR for dashboard |
| Hosting | Vercel (free tier) | Zero config deploy, free SSL, global CDN |
| UI Components | shadcn/ui + Tailwind CSS | No licensing, beautiful, fast to build |
| Charts | Recharts | Free, React-native, sufficient for dashboards |
| State | Zustand | Lightweight, no Redux complexity |
| Auth UI | Supabase Auth UI | Pre-built, matches our auth backend |
| Real-time | Supabase Realtime subscriptions | Dashboard updates without polling |

### Backend
| Layer | Choice | Reason |
|---|---|---|
| Framework | FastAPI (Python) | Your core stack, async-first, auto docs |
| Hosting | Railway (free tier, $5/mo after) | Simplest FastAPI deploy, no Dockerfile needed |
| Task Queue | Inngest (free tier) or Supabase pg_cron | Background eval jobs, no Redis worker needed at free tier |
| API Validation | Pydantic v2 | Already in your stack |
| HTTP Client | httpx (async) | Non-blocking outbound calls |

### Database & Storage
| Layer | Choice | Reason |
|---|---|---|
| Primary DB | Supabase PostgreSQL (free: 500MB) | Traces, scores, users, pipelines, API keys |
| Vector Store | Qdrant Cloud (free: 1GB) | Chunk embeddings for similarity analysis |
| Cache | Upstash Redis (free: 10K cmds/day) | API key validation, rate limiting, session cache |
| File Storage | Supabase Storage (free: 1GB) | Agent test reports, eval exports |
| Auth | Supabase Auth | JWT-based, free, handles all user management |

### AI / LLM Layer
| Use | Model | Cost |
|---|---|---|
| Eval computation (faithfulness, relevance, hallucination) | Claude Haiku | ~$0.001 per 1000 queries |
| Adversarial test scenario generation | Claude Haiku | ~$0.002 per 10 scenarios |
| Chunk quality analysis | Claude Haiku | Negligible |

**Why Claude Haiku:** Cheapest capable model for structured eval tasks. Output is JSON-structured, fast, and accurate enough for eval scoring. Users can optionally bring their own API key to use their own model.

### SDK
| Target | Package | Install |
|---|---|---|
| Python (any RAG) | kairoslabs | pip install kairoslabs |
| LangChain | kairoslabs[langchain] | pip install kairoslabs[langchain] |
| LlamaIndex | kairoslabs[llamaindex] | pip install kairoslabs[llamaindex] |
| JavaScript | @kairos/sdk | npm install @kairos/sdk |

### DevOps / Infrastructure
| Tool | Use | Cost |
|---|---|---|
| GitHub | Version control, CI/CD via Actions | Free |
| Vercel | Frontend deploy | Free |
| Railway | Backend deploy | Free tier, $5/mo after |
| Docker Compose | Self-hosting option | Free |
| GitHub Actions | CI: lint, test, deploy | Free (2000 min/mo) |

**Total infrastructure cost at launch: ₹0**

---

## 6. Repository Structure

```
kairos/
├── packages/
│   ├── sdk-python/                  # Core Python SDK
│   │   ├── kairos/
│   │   │   ├── __init__.py
│   │   │   ├── tracer.py            # KairosTracer — RAG wrapping
│   │   │   ├── agent_tester.py      # AgentTester — reliability testing
│   │   │   ├── integrations/
│   │   │   │   ├── langchain.py     # LangChain callback handler
│   │   │   │   ├── llamaindex.py    # LlamaIndex observer
│   │   │   │   └── custom.py        # Generic wrapper for any retriever
│   │   │   ├── models.py            # Pydantic trace/score models
│   │   │   ├── client.py            # Async HTTP client to Kairos API
│   │   │   └── utils.py
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── README.md
│   │
│   └── sdk-js/                      # JavaScript SDK (Phase 2)
│       ├── src/
│       │   ├── index.ts
│       │   ├── tracer.ts
│       │   └── client.ts
│       ├── package.json
│       └── README.md
│
├── apps/
│   ├── api/                         # FastAPI backend
│   │   ├── main.py
│   │   ├── routers/
│   │   │   ├── ingest.py            # POST /v1/traces  — receive traces
│   │   │   ├── pipelines.py         # CRUD for user pipelines
│   │   │   ├── agents.py            # Agent test management
│   │   │   ├── evals.py             # Eval score retrieval
│   │   │   ├── auth.py              # API key management
│   │   │   └── health.py            # Health check
│   │   ├── services/
│   │   │   ├── eval_service.py      # Claude Haiku eval computation
│   │   │   ├── trace_service.py     # Trace storage and retrieval
│   │   │   ├── agent_sim.py         # Agent simulation runner
│   │   │   ├── scenario_gen.py      # Adversarial scenario generation
│   │   │   └── alerting.py          # Degradation detection + alerts
│   │   ├── models/
│   │   │   ├── trace.py
│   │   │   ├── eval.py
│   │   │   ├── pipeline.py
│   │   │   └── user.py
│   │   ├── db/
│   │   │   ├── supabase.py          # Supabase client
│   │   │   └── migrations/          # SQL migration files
│   │   ├── workers/
│   │   │   └── eval_worker.py       # Background eval job processor
│   │   ├── config.py                # Settings via pydantic-settings
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   └── dashboard/                   # Next.js frontend
│       ├── app/
│       │   ├── (auth)/
│       │   │   ├── login/page.tsx
│       │   │   └── signup/page.tsx
│       │   ├── (dashboard)/
│       │   │   ├── layout.tsx
│       │   │   ├── page.tsx             # Overview / home
│       │   │   ├── pipelines/
│       │   │   │   ├── page.tsx         # Pipeline list
│       │   │   │   └── [id]/page.tsx    # Pipeline detail + health graph
│       │   │   ├── traces/
│       │   │   │   ├── page.tsx         # Query explorer
│       │   │   │   └── [id]/page.tsx    # Single trace detail
│       │   │   ├── agents/
│       │   │   │   ├── page.tsx         # Agent test runs
│       │   │   │   └── [id]/page.tsx    # Test run detail + reliability score
│       │   │   ├── chunks/page.tsx      # Chunk graveyard
│       │   │   └── settings/page.tsx    # API keys, account
│       │   └── layout.tsx
│       ├── components/
│       │   ├── charts/
│       │   │   ├── PipelineHealthChart.tsx
│       │   │   ├── ScoreTimeline.tsx
│       │   │   └── FailureClusterView.tsx
│       │   ├── traces/
│       │   │   ├── TraceTable.tsx
│       │   │   └── TraceDetail.tsx
│       │   └── ui/                  # shadcn components
│       ├── lib/
│       │   ├── supabase.ts
│       │   └── api.ts
│       ├── package.json
│       └── next.config.js
│
├── docs/                            # Documentation site (Mintlify or Docusaurus)
│   ├── quickstart.mdx
│   ├── sdk-reference.mdx
│   ├── self-hosting.mdx
│   └── concepts.mdx
│
├── docker-compose.yml               # Full self-host stack
├── docker-compose.dev.yml           # Local development
├── .github/
│   └── workflows/
│       ├── test.yml                 # Run tests on PR
│       └── deploy.yml               # Deploy on merge to main
├── ARCHITECTURE.md                  # This file
├── CONTRIBUTING.md
├── LICENSE                          # Apache 2.0
└── README.md
```

---

## 7. Database Schema (Supabase PostgreSQL)

```sql
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
```

### v1.1 schema additions (migrations 0002 + 0003 — the migration files are canonical)

- **Row Level Security enabled on every table** with owner-scoped policies
  (`user_id = auth.uid()`, joined through `pipelines`/`agent_test_runs` where
  needed). The service-role key used by the API/worker bypasses RLS; the
  dashboard reads Supabase directly under these policies.
- **`handle_new_user()` trigger on `auth.users`** — auto-creates the `profiles`
  row on signup (signup is broken without it).
- **`traces.eval_status`** (`'pending'|'completed'|'failed'|'skipped'`, partial
  index on pending) + **`traces.eval_attempts`** — status-driven eval queue with
  bounded retries.
- **`eval_scores` UNIQUE(trace_id)** — a worker race can never double-score.
- **`eval_scores.prompt_version`** — score comparability across prompt revisions.
- **`pipelines.eval_sample_rate`** (0.00–1.00) — per-pipeline eval sampling.
- **`pipeline_health_daily`** — nightly health snapshots (score, per-metric
  averages, eval/hallucination counts) enabling week-over-week alerting.
- **RPCs:** `consume_trace_quota(user, count, limit)` (atomic monthly metering)
  and `upsert_chunk_retrieval(...)` (atomic retrieval-count increment).
- **pg_cron jobs (0003):** nightly 30-day trace purge + nightly health rollup.

---

## 8. API Design

**Base URL:** `https://api.kairos.dev/v1`

**Authentication — two caller types (v1.1):**
- **SDK/ingest routes** authenticate with `Authorization: Bearer kai_live_<key>` (API key; hashed at rest, validated via Redis cache).
- **Dashboard routes** authenticate with the user's **Supabase Auth JWT** — the raw API key is shown once at creation and never stored, so the frontend cannot use it.
- Health check is unauthenticated.

**Dashboard data access (v1.1):** the Next.js dashboard reads `pipelines`, `traces`, `eval_scores`, `chunk_index`, `alerts`, `usage`, and `pipeline_health_daily` **directly from Supabase** using the user's JWT, protected by Row Level Security policies (migration 0002) — this is also what makes Supabase Realtime subscriptions safe. FastAPI keeps only the routes that need server-side logic (ingest, key management, mutations with side effects). The read-only GET routes crossed out below are therefore not implemented in FastAPI.

**Rate limits & quotas (v1.1):** ingest routes enforce a per-key rate limit (default 120 req/min, Upstash counter) and the per-user monthly trace quota (default 10,000; atomic `consume_trace_quota` RPC). Both return 429.

### Ingest Endpoints (called by SDK — API key auth)

```
POST /v1/traces
  Body: {pipeline_id, query, retrieved_chunks, final_answer,
         latency_ms, token_count_input, token_count_output,
         estimated_cost_usd, metadata}
  Response: {trace_id, status: "queued"}

POST /v1/traces/batch                       — v1.1: the SDK buffers and sends batches
  Body: {traces: [<trace>, ...]}            — 1..100 traces per batch
  Response: {trace_ids: [...], status: "queued"}

POST /v1/agent/simulations
  Body: {test_run_id, scenario_type, input_message,
         agent_output, tool_calls, steps_taken, latency_ms}
  Response: {simulation_id, status: "queued"}
```

### Dashboard Endpoints (called by frontend — Supabase JWT auth)

```
POST /v1/pipelines                          — create pipeline (or direct via RLS)
GET  /v1/pipelines/{id}/failures            — failure clusters (computed)

POST /v1/agent/projects                     — create agent project
POST /v1/agent/projects/{id}/test-runs      — start a test run
GET  /v1/agent/test-runs/{id}               — test run status + results

PUT  /v1/alerts/{id}/resolve                — mark alert resolved

GET  /v1/keys                               — list API keys
POST /v1/keys                               — create API key (raw key returned once)
DELETE /v1/keys/{id}                        — revoke API key (immediate: Redis cache invalidated)
```

Plain reads previously listed here (pipeline list/detail, health-over-time, trace
lists, trace detail, chunk graveyard, alerts list, usage) are served by direct
Supabase queries under RLS instead of FastAPI endpoints.

---

## 9. SDK Design

### Python SDK — RAG Tracing

**Minimal integration (any custom retriever):**
```python
from kairos import KairosTracer

tracer = KairosTracer(api_key="kai_live_xxxx")
retriever = tracer.wrap(your_retriever)
# Now use retriever normally — tracing is automatic and async
```

**LangChain integration:**
```python
from kairos.integrations.langchain import KairosCallbackHandler

handler = KairosCallbackHandler(
    api_key="kai_live_xxxx",
    pipeline_id="your-pipeline-id"
)

chain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=retriever,
    callbacks=[handler]   # one line addition
)
```

**LlamaIndex integration:**
```python
from kairos.integrations.llamaindex import KairosObserver

observer = KairosObserver(api_key="kai_live_xxxx")
observer.attach()   # globally attaches to all LlamaIndex queries
```

**Manual trace (if you want full control):**
```python
with tracer.trace(query=user_query) as t:
    chunks = your_retriever.retrieve(user_query)
    t.log_retrieval(chunks)
    answer = your_llm.generate(chunks, user_query)
    t.log_answer(answer)
```

### Python SDK — Agent Testing

```python
from kairos import AgentTester

tester = AgentTester(api_key="kai_live_xxxx")

# Auto-generates scenarios + runs simulations
results = await tester.run(
    agent=your_langgraph_agent,      # any callable agent
    agent_project_id="proj-xxx",
    scenarios="auto",                # or pass custom list
    n_runs=100,                      # simulations to run
    parallel=10                      # concurrent simulations
)

print(f"Reliability score: {results.reliability_score}/100")
print(f"Task completion rate: {results.task_completion_rate}%")
print(f"Top failure mode: {results.top_failure_mode}")
print(f"Full report: {results.dashboard_url}")
```

---

## 10. Eval Computation Logic

### RAG Eval (Phase 1)

All computed by Claude Haiku in background after trace ingestion.

**Faithfulness (0–1)**
Measures: Does the answer only contain information present in the retrieved chunks?
Method: Claude Haiku receives (answer, chunks), returns faithfulness score + flagged sentences that aren't grounded.

**Answer Relevance (0–1)**
Measures: Does the answer actually address what the user asked?
Method: Claude Haiku receives (query, answer), scores relevance.

**Context Precision (0–1)**
Measures: Were the retrieved chunks actually useful for answering the query?
Method: Claude Haiku receives (query, chunks, answer), scores what fraction of chunks contributed.

**Hallucination Flag (boolean + detail)**
Measures: Did the model make claims not supported by any retrieved chunk?
Method: Derived from faithfulness — if faithfulness < 0.6 AND specific unsupported claims detected, flag as hallucination with detail.

**Failure Category (enum)**
When any score is below threshold, Claude Haiku classifies the root cause:
- `chunking` — chunks were too large/small, lost context
- `embedding` — semantically wrong chunks retrieved
- `reranking` — right chunks retrieved but wrong ones ranked top
- `prompt` — context was fine but prompt lost information
- `model` — model ignored context and used training knowledge

**Pipeline Health Score (0–100)**
Computed nightly per pipeline into `pipeline_health_daily` (pg_cron job, migration 0003):
`((faithfulness * 0.35) + (answer_relevance * 0.35) + (context_precision * 0.30)) * 100`
Degradation alert triggers if score drops >15% week-over-week (compared against the daily snapshots).

**Eval sampling & caps (v1.1 — cost control)**
Each pipeline has an `eval_sample_rate` (default 1.00 = every trace scored). The worker also
enforces a per-user daily eval cap (default 2,000) as a spend backstop; traces beyond the
sample/cap are marked `eval_status='skipped'` rather than scored. Every eval_scores row is
stamped with `model_used` and `prompt_version` so scores stay comparable across prompt revisions.

### Agent Eval (Phase 2)

**Reliability Score (0–100)**
`(task_completion_rate * 0.40) + (tool_accuracy_rate * 0.30) + (no_hallucination_rate * 0.20) + (no_loop_rate * 0.10) * 100`

**Scenario Types Generated:**
- Normal — typical happy path inputs
- Adversarial — inputs designed to confuse the agent
- Edge case — boundary conditions, empty inputs, very long inputs
- Prompt injection — attempts to hijack agent behavior
- Off-topic — inputs outside the agent's scope

---

## 11. Data Flow

### RAG Trace Flow
```
User's app calls retriever
    → SDK intercepts (async, non-blocking)
    → POST /v1/traces (fire and forget)
    → User's app continues normally (zero latency impact)

API receives trace (single or batch)
    → Validates API key (Upstash Redis cache)
    → Enforces per-key rate limit (Redis counter) and monthly quota (consume_trace_quota RPC)
    → Writes raw trace(s) to Supabase traces table with eval_status='pending'
    → Returns {trace_id(s), status: "queued"} immediately

Eval worker (status-driven poll on eval_status='pending'; no external queue —
Inngest deliberately not used, the worker ships as its own process)
    → Applies pipeline eval_sample_rate + per-user daily eval cap (else 'skipped')
    → Builds eval prompt for Claude Haiku (structured output)
    → Calls Claude Haiku API
    → Writes scores to eval_scores (UNIQUE per trace; stamped model_used + prompt_version)
    → Updates chunk_index retrieval counts (atomic upsert RPC)
    → Marks trace 'completed' — on error retries up to eval_max_attempts, then 'failed'
    → Checks alert thresholds
    → Triggers alert if threshold breached

Nightly (pg_cron, migration 0003)
    → 30-day trace retention purge (free-tier promise; keeps DB within limits)
    → pipeline_health_daily rollup (feeds health charts + week-over-week alerts)

Dashboard
    → Supabase Realtime subscription shows new trace + scores
    → No polling needed
```

### Agent Test Flow
```
User calls AgentTester.run()
    → SDK sends agent metadata to POST /v1/agent/projects
    → API generates adversarial scenarios via Claude Haiku
    → Returns scenario list + test_run_id

SDK receives scenarios
    → Runs N parallel simulated interactions
    → Each interaction: scenario input → agent → capture output + tool calls
    → POST /v1/agent/simulations for each result (batched)

API processes simulation results
    → Scores each simulation (completed? hallucinated? looped?)
    → Aggregates reliability score for test run
    → Updates test_run status to 'completed'

SDK receives final results
    → Returns ReliabilityReport object to user
    → Dashboard shows full breakdown
```

---

## 12. Build Phases

### Phase 1 — RAG Observability (Weeks 1–4)
**Goal:** Working product, installable SDK, live dashboard, first 10 real users

Week 1:
- FastAPI project scaffold
- Supabase schema + migrations
- Ingest endpoint (`POST /v1/traces`)
- API key generation + validation
- Railway deployment

Week 2:
- Python SDK core (`KairosTracer`, async HTTP client)
- LangChain callback handler
- `pip install kairoslabs` working on PyPI (kairos-ai/kairos were taken — verified 2026-07-12)
- SDK tested against a real LangChain RAG pipeline

Week 3:
- Eval worker (Claude Haiku integration)
- Faithfulness, relevance, context precision scoring
- Hallucination detection
- Failure category classification

Week 4:
- Next.js dashboard scaffold on Vercel
- Pipeline health chart
- Query explorer table
- Single trace detail view
- Supabase Auth (login/signup)
- Basic alert system

**Launch checklist:**
- [ ] Docker Compose self-host works end to end
- [ ] README has working demo GIF
- [ ] Quickstart doc: from zero to first trace in under 5 minutes
- [ ] Eval quality validated on 50 known-good and known-bad traces
- [ ] Posted to LangChain Discord, r/LocalLLaMA, Hugging Face forums

### Phase 2 — Agent Reliability Testing (Weeks 5–8)
**Goal:** Agent testing live, unified dashboard, first 50 users

Week 5:
- Scenario generation service (Claude Haiku)
- AgentTester class in SDK
- Agent simulation runner (parallel, async)
- `POST /v1/agent/simulations` endpoint

Week 6:
- Reliability score computation
- Test run management API
- Agent projects dashboard tab
- Test run detail + failure breakdown view

Week 7:
- Prompt injection scenario type
- Chunk graveyard view
- Failure cluster grouping
- Week-over-week degradation alerts

Week 8:
- LlamaIndex SDK integration
- JS SDK basic version
- Docs site (Mintlify free tier)
- ProductHunt launch preparation

### Phase 3 — Unified Intelligence (Month 3+)
**Goal:** Pre/post deployment correlation, paid tier, first ₹10K MRR

- Correlation dashboard: test reliability score vs production health
- Input distribution drift detection
- Automated test gap suggestions ("your production queries aren't covered by your test scenarios")
- Paid tier: Stripe integration, unlimited usage, team seats, Slack alerts
- Enterprise: self-host license, SSO, audit logs

---

## 13. Environment Variables

### API (.env)
```
# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=xxxx         # server-side only, never expose

# Upstash Redis
UPSTASH_REDIS_REST_URL=xxxx
UPSTASH_REDIS_REST_TOKEN=xxxx

# Anthropic (for eval computation)
ANTHROPIC_API_KEY=xxxx

# Inngest (background jobs)
INNGEST_EVENT_KEY=xxxx
INNGEST_SIGNING_KEY=xxxx

# App
APP_ENV=production
API_BASE_URL=https://api.kairos.dev
FRONTEND_URL=https://app.kairos.dev

# v1.1 additions (all optional, sane defaults)
SUPABASE_ANON_KEY=xxxx                 # used by RLS verification tests; the dashboard has its own copy
RATE_LIMIT_PER_MINUTE=120              # per API key, ingest routes
FREE_TIER_TRACES_PER_MONTH=10000       # per user
EVAL_MAX_ATTEMPTS=3                    # then trace marked 'failed'
EVAL_DAILY_CAP_PER_USER=2000           # Haiku spend backstop
```

### Dashboard (.env.local)
```
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=xxxx     # public anon key, safe to expose
NEXT_PUBLIC_API_URL=https://api.kairos.dev
```

### SDK (user's .env)
```
KAIROS_API_KEY=kai_live_xxxx           # SDK reads this automatically
KAIROS_PIPELINE_ID=xxxx                # optional, can pass in code
```

---

## 14. Self-Hosting (docker-compose.yml)

For users who want to run Kairos on their own infrastructure (important for open source credibility and enterprise adoption).

The API and worker speak Upstash's **REST** protocol, not the Redis wire
protocol — so the self-host stack fronts a standard Redis with Upstash's
open-source SRH proxy (`hiett/serverless-redis-http`). No Upstash account
needed to self-host. Note the self-host stack still points at a Supabase
project (cloud or self-hosted Supabase) for Postgres + Auth — stated
explicitly so self-hosters aren't surprised.

```yaml
version: '3.8'
services:
  api:
    build: ./apps/api
    ports:
      - "8000:8000"
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - UPSTASH_REDIS_REST_URL=http://srh:80
      - UPSTASH_REDIS_REST_TOKEN=${SRH_TOKEN:-kairos-selfhost-token}
    depends_on:
      - srh

  dashboard:
    build: ./apps/dashboard
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL}
      - NEXT_PUBLIC_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
      - NEXT_PUBLIC_API_URL=http://localhost:8000

  redis:
    image: redis:7-alpine

  srh:  # Upstash-compatible REST facade over the local Redis
    image: hiett/serverless-redis-http:latest
    environment:
      - SRH_MODE=env
      - SRH_TOKEN=${SRH_TOKEN:-kairos-selfhost-token}
      - SRH_CONNECTION_STRING=redis://redis:6379
    depends_on:
      - redis

  worker:
    build: ./apps/api
    command: python -m workers.eval_worker
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - UPSTASH_REDIS_REST_URL=http://srh:80
      - UPSTASH_REDIS_REST_TOKEN=${SRH_TOKEN:-kairos-selfhost-token}
    depends_on:
      - srh
```

---

## 15. Cost Analysis

### Free Tier Stack Limits
| Service | Free Limit | Enough For |
|---|---|---|
| Supabase DB | 500MB | ~2M traces |
| Supabase Auth | Unlimited users | Fine |
| Supabase Realtime | 200 concurrent | Fine for MVP |
| Vercel | 100GB bandwidth | Fine |
| Railway | $5 free credit/mo | Covers light traffic |
| Upstash Redis | 10K commands/day | ~10K API calls/day |
| Qdrant Cloud | 1GB vectors | ~1M chunk embeddings |
| Anthropic Haiku | Pay as you go | ~$0.001/trace |
| Inngest | 50K events/mo free | Fine for MVP |

### Claude Haiku Cost Per Operation (corrected, v1.1)
- Eval one RAG trace: **~$0.002–0.004** (≈1.5–2.5K input tokens for system prompt +
  chunks + answer, ≈200 output, at Haiku's $1/$5 per MTok)
- Generate 10 agent scenarios: ~$0.002
- Score one agent simulation: ~$0.0005

One user maxing the free tier (10,000 traces/month) ≈ **$20–40/month** in eval cost.
100 active free users at full utilization ≈ **$2,000–4,000/month** — NOT $15–20.
Real usage will be far below full utilization, but eval spend must be bounded by
design, not by hope. Cost controls (v1.1):

1. **Per-pipeline eval sampling** (`eval_sample_rate`, default 1.00) — high-volume
   pipelines don't need every trace scored to show accurate trends.
2. **Per-user daily eval cap** (default 2,000/day) — hard spend backstop; over-cap
   traces are marked 'skipped', never silently billed.
3. **Monthly trace quota enforced at ingest** (10,000/month free tier) — the paid
   eval call can never be triggered beyond the metered allowance.
4. Planned: **Anthropic Message Batches API** for eval jobs (50% discount — evals
   are already async) and **bring-your-own-Anthropic-key** per user.

**Bounded worst-case platform eval cost ≈ (active users) × (daily cap) × ($0.003) — and
in practice one to two orders of magnitude below that.**

---

## 16. Key Technical Decisions & Rationale

**Why Supabase over Firebase?**
PostgreSQL gives us real relational queries for complex eval analytics. Supabase Realtime is excellent. Free tier is generous. Self-hostable.

**Why FastAPI over Node/Express?**
Your core stack. Python gives access to all ML/eval libraries natively. Pydantic validation is first-class. Auto-generated OpenAPI docs.

**Why Claude Haiku for evals and not open source models?**
Haiku is fast, cheap, and accurate enough for structured eval tasks. Running a local model (Ollama) would work for self-hosted but adds complexity for the cloud version. Users can bring their own model key as a config option later.

**Why async SDK?**
Tracing must never add latency to the user's pipeline. Fire-and-forget async HTTP calls mean zero performance impact.

**Why open source?**
Faster trust building in developer community. Langfuse went from 0 to 5000 GitHub stars in 4 months with this approach. Self-hosting option removes the "I can't send my data to a third party" objection from enterprise users.

**Why Apache 2.0 license?**
Permissive. Companies can use it in commercial products without worry. Maximizes adoption. Paid cloud features stay closed source.

---

## 17. Competitive Positioning

| Tool | What They Do | Gap Kairos Fills |
|---|---|---|
| LangSmith | Traces LLM calls, shows inputs/outputs | No continuous eval scoring, no root cause, expensive |
| Arize AI | Post-deploy ML monitoring | Built for classical ML, bolted GenAI on, enterprise pricing |
| RAGAS | Eval framework (code library) | Not a product, manual point-in-time, no dashboard |
| DeepEval | Eval framework (code library) | Same as RAGAS |
| Helicone | LLM call logging + cost tracking | Logs only, no eval, no retrieval analysis |
| Langfuse | Open source LLM observability | Closest competitor — no agent reliability testing, no failure root cause |

**Kairos's unique position:**
Only product that combines (1) continuous automated RAG eval with root cause, (2) pre-deployment agent reliability testing, and (3) post-deployment correlation between test scores and production health — in one open source platform at zero cost to start.

---

## 18. Success Metrics (First 3 Months)

| Metric | Target |
|---|---|
| GitHub stars | 500+ |
| Discord/Slack members | 200+ |
| Active users (at least 1 trace/week) | 100+ |
| PyPI downloads | 1000+/month |
| ProductHunt ranking | Top 5 on launch day |
| Eval quality (faithfulness score accuracy) | >85% match with human judgment |
| First paid user | By Month 3 |

---

*Last updated: July 2026*
*Version: 1.1 — hardening revision from the 2026-07-12 architecture review:
RLS + split auth model (API key for SDK, Supabase JWT for dashboard, direct
Supabase reads under RLS), ingest rate limiting + monthly quota, eval cost
controls (sampling, daily caps, corrected cost math), status-driven eval
worker with bounded retries, 30-day retention + nightly health rollups via
pg_cron, profiles auto-creation trigger, immediate key revocation, SDK
batching, self-host Redis via SRH proxy.*
*Author: Atharv Puranik*
