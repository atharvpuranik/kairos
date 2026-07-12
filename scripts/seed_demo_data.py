"""Seeds realistic, PERSISTENT demo data for the demo@kairos.local account.

Unlike scripts/capture_demo.py (which tears everything down after capturing
screenshots), this leaves the data in place so you can browse the dashboard
with something real to look at. Safe to re-run — it wipes and rebuilds only
the data owned by demo@kairos.local.

Creates 3 pipelines with different frameworks/sample rates, ~180 traces
spread over the last 30 days covering every case the eval/alerting logic
recognizes (grounded, hallucinated, off-topic, wrong-chunks, partial —
across all 5 failure categories), every trace eval_status (completed,
pending, skipped, failed), 30 days of health history per pipeline with
distinct trends (stable, degrading, improving), and a mix of active +
resolved alerts.

Run: apps/api/.venv/bin/python scripts/seed_demo_data.py
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"

DEMO_EMAIL = "demo@kairos.local"
RNG = random.Random(42)


def load_admin():
    from supabase import create_client

    env = dotenv_values(API_DIR / ".env")
    return create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])


CHUNK_BANK = [
    "Kairos retains traces for 30 days on the free tier, then purges them automatically via a nightly job.",
    "The Python SDK buffers traces in a background thread and flushes every 20 items or 2 seconds, whichever comes first.",
    "API keys use the kai_live_ prefix and are shown once at creation; revocation takes effect immediately via cache invalidation.",
    "Faithfulness measures whether every claim in the answer is grounded in the retrieved chunks, scored 0 to 1.",
    "A degradation alert fires when faithfulness drops more than 15% versus the trailing 7-day baseline.",
    "The free tier includes 10,000 traced queries per month per workspace; overage requests return 429.",
    "Self-hosting ships as a Docker Compose stack with an SRH proxy so no Upstash account is required.",
    "The eval worker polls for eval_status='pending' traces every 5 seconds and scores them via the configured LLM provider.",
    "Row Level Security means every table is scoped to auth.uid() — no user can read another workspace's data.",
    "Rate limiting is a fixed 60-second window per API key, tracked in Redis via INCR with a TTL.",
]

GOOD_QA = [
    ("How long are traces retained?", "Traces are retained for 30 days on the free tier and purged automatically by a nightly job.", 0),
    ("How does the SDK avoid blocking my pipeline?", "It buffers traces in a background thread and flushes them in batches, so ingestion never blocks your request path.", 1),
    ("What happens when I revoke an API key?", "Revocation is immediate — the Redis cache entry is deleted so the key stops working right away, not after a TTL.", 2),
    ("What does the faithfulness score measure?", "It measures whether every claim in the final answer is grounded in the retrieved chunks, scored from 0 to 1.", 3),
    ("When does a degradation alert fire?", "A degradation alert fires when faithfulness drops more than 15% versus the trailing 7-day baseline.", 4),
    ("What's the free tier trace limit?", "The free tier includes 10,000 traced queries per month per workspace; requests over that return 429.", 5),
    ("Can I self-host this?", "Yes, self-hosting ships as a Docker Compose stack with a bundled Redis-compatible proxy, no cloud Redis account needed.", 6),
    ("How often does the eval worker check for new traces?", "It polls every 5 seconds for traces with eval_status='pending' and scores them.", 7),
    ("Is my data isolated from other workspaces?", "Yes, Row Level Security scopes every table to your user id — no cross-tenant reads are possible.", 8),
    ("How is rate limiting implemented?", "It's a fixed 60-second window per API key, tracked in Redis with INCR and a TTL.", 9),
]

HALLUCINATED_QA = [
    ("Do you offer a dedicated account manager?", "Yes, every plan includes a dedicated account manager, 24/7 phone support, and a guaranteed 15-minute SLA.", "model", "Invented enterprise support tiers and an SLA number not present anywhere in the retrieved context."),
    ("Is there a mobile app?", "Yes, Kairos has native iOS and Android apps available on the App Store and Google Play.", "model", "No mobile app exists or is mentioned in the retrieved chunks — fabricated from general SaaS assumptions."),
    ("What's the uptime SLA?", "Kairos guarantees 99.99% uptime with financial credits for any downtime beyond that.", "model", "No SLA figure appears in any retrieved chunk; the number was invented."),
    ("Do you support on-prem deployment for enterprise?", "Yes, the enterprise tier includes air-gapped on-prem deployment with a dedicated infrastructure team.", "model", "Enterprise/on-prem tier details are not present in the retrieved chunks."),
    ("How many regions do you deploy to?", "Kairos runs across 12 global regions with automatic failover between them.", "model", "Multi-region deployment claims are fabricated; not supported by any retrieved chunk."),
]

OFFTOPIC_QA = [
    ("What is the free tier trace limit?", "Our recommended embedding model is text-embedding-3-small for most RAG use cases.", "embedding"),
    ("How do I revoke an API key?", "Faithfulness is computed by comparing the answer against retrieved chunks on a 0-1 scale.", "embedding"),
    ("What's the rate limit window?", "The SDK flushes its buffer every 20 items or 2 seconds, whichever happens first.", "embedding"),
    ("Does self-hosting need Redis?", "Traces are purged automatically after 30 days via a nightly cron job.", "reranking"),
]

WRONGCHUNKS_QA = [
    ("How do alerts get deduplicated?", "chunking", "The chunk actually relevant to alert dedupe logic was never retrieved — only unrelated pricing/SDK chunks came back."),
    ("What triggers a hallucination_spike alert?", "chunking", "None of the retrieved chunks mention the hallucination-rate threshold or multiplier; wrong chunks were surfaced."),
    ("How does chunk_index track retrieval counts?", "chunking", "The retrieved chunks don't cover chunk_index at all — an unrelated set was returned by the retriever."),
]

PARTIAL_QA = [
    ("What's included in the free tier?", "The free tier includes 10,000 traces per month, and also comes with a free custom domain and unlimited team seats.", "prompt", "The trace limit is correct but the domain/seats claims aren't grounded in the retrieved context."),
    ("How does revocation work?", "Revocation deletes the Redis cache entry immediately, and also automatically emails all workspace admins a security report.", "prompt", "Cache invalidation is correct; the admin-email claim is unsupported."),
]

FRAMEWORKS = ["langchain", "llamaindex", "custom"]


def make_chunks(n: int, wrong: bool = False) -> list[dict]:
    pool = CHUNK_BANK if not wrong else list(reversed(CHUNK_BANK))
    chosen = RNG.sample(pool, k=min(n, len(pool)))
    return [
        {"content": c, "score": round(RNG.uniform(0.55, 0.97), 3), "doc_id": f"docs/kb.md#{i}", "metadata": None}
        for i, c in enumerate(chosen)
    ]


def gen_trace_case(now: datetime, pipeline_id: str, user_id: str, hours_ago: float) -> tuple[dict, dict | None]:
    """Returns (trace_row, eval_score_row_or_None)."""
    bucket = RNG.random()
    ts = (now - timedelta(hours=hours_ago)).isoformat()
    latency = RNG.randint(120, 950)
    tin, tout = RNG.randint(400, 2200), RNG.randint(40, 380)
    cost = round(tin * 0.0000008 + tout * 0.000004, 6)

    if bucket < 0.55:
        q, a, _ = RNG.choice(GOOD_QA)
        f = round(RNG.uniform(0.85, 0.99), 3)
        eval_row = dict(faithfulness=f, answer_relevance=round(RNG.uniform(0.85, 0.99), 3),
                        context_precision=round(RNG.uniform(0.75, 0.97), 3),
                        hallucination_flag=False, hallucination_detail=None,
                        failure_category=None, failure_reason=None)
        chunks = make_chunks(RNG.randint(2, 4))
    elif bucket < 0.70:
        q, a, cat, detail = RNG.choice(HALLUCINATED_QA)
        f = round(RNG.uniform(0.02, 0.25), 3)
        eval_row = dict(faithfulness=f, answer_relevance=round(RNG.uniform(0.6, 0.9), 3),
                        context_precision=round(RNG.uniform(0.1, 0.4), 3),
                        hallucination_flag=True, hallucination_detail=detail,
                        failure_category=cat, failure_reason=f"The model answered from prior knowledge instead of the retrieved context: {detail}")
        chunks = make_chunks(RNG.randint(1, 3))
    elif bucket < 0.82:
        q, a, cat = RNG.choice(OFFTOPIC_QA)
        eval_row = dict(faithfulness=round(RNG.uniform(0.5, 0.8), 3), answer_relevance=round(RNG.uniform(0.05, 0.3), 3),
                        context_precision=round(RNG.uniform(0.1, 0.35), 3),
                        hallucination_flag=False, hallucination_detail=None,
                        failure_category=cat, failure_reason="Retrieved chunks were semantically unrelated to the query — the wrong content was surfaced.")
        chunks = make_chunks(RNG.randint(2, 3), wrong=True)
    elif bucket < 0.92:
        q, cat, reason = RNG.choice(WRONGCHUNKS_QA)
        a = "Based on the available information, I don't have enough context to answer that precisely."
        eval_row = dict(faithfulness=round(RNG.uniform(0.3, 0.6), 3), answer_relevance=round(RNG.uniform(0.4, 0.65), 3),
                        context_precision=round(RNG.uniform(0.05, 0.25), 3),
                        hallucination_flag=False, hallucination_detail=None,
                        failure_category=cat, failure_reason=reason)
        chunks = make_chunks(RNG.randint(2, 4), wrong=True)
    else:
        q, a, cat, reason = RNG.choice(PARTIAL_QA)
        eval_row = dict(faithfulness=round(RNG.uniform(0.55, 0.69), 3), answer_relevance=round(RNG.uniform(0.7, 0.9), 3),
                        context_precision=round(RNG.uniform(0.5, 0.7), 3),
                        hallucination_flag=False, hallucination_detail=None,
                        failure_category=cat, failure_reason=reason)
        chunks = make_chunks(RNG.randint(2, 3))

    reranked = make_chunks(min(len(chunks), 2)) if RNG.random() < 0.3 else None
    metadata = {"env": RNG.choice(["prod", "staging"]), "region": RNG.choice(["us-east-1", "eu-west-1"])} if RNG.random() < 0.4 else None

    trace = {
        "pipeline_id": pipeline_id,
        "user_id": user_id,
        "query": q,
        "retrieved_chunks": chunks,
        "reranked_chunks": reranked,
        "final_answer": a,
        "latency_ms": latency,
        "token_count_input": tin,
        "token_count_output": tout,
        "estimated_cost_usd": str(cost),
        "metadata": metadata,
        "created_at": ts,
    }
    return trace, eval_row


def seed_pipeline_traces(admin, pipeline_id: str, user_id: str, n: int, days: int, now: datetime) -> dict:
    trace_rows = []
    eval_rows_by_index = {}
    for i in range(n):
        hours_ago = RNG.uniform(0.1, days * 24)
        trace, eval_row = gen_trace_case(now, pipeline_id, user_id, hours_ago)
        trace_rows.append(trace)
        if eval_row is not None:
            eval_rows_by_index[i] = eval_row

    inserted = admin.table("traces").insert(trace_rows).execute().data
    trace_ids = [row["id"] for row in inserted]

    # Assign final eval_status: most 'completed' (with a score), a few
    # 'pending' (very recent, not yet picked up), 'skipped' (sampled out),
    # and 'failed' (exhausted retries) — covering every status the traces
    # list/filter UI can show.
    eval_score_rows = []
    status_updates = {"pending": [], "skipped": [], "failed": []}
    for i, trace_id in enumerate(trace_ids):
        roll = RNG.random()
        if roll < 0.06:
            status_updates["pending"].append(trace_id)
            continue
        if roll < 0.10:
            status_updates["skipped"].append(trace_id)
            continue
        if roll < 0.13:
            status_updates["failed"].append(trace_id)
            continue
        eval_row = eval_rows_by_index.get(i)
        if eval_row is None:
            status_updates["pending"].append(trace_id)
            continue
        eval_score_rows.append({
            "trace_id": trace_id,
            "pipeline_id": pipeline_id,
            "model_used": "gemini-flash-lite-latest",
            "prompt_version": "v1",
            **eval_row,
        })

    if eval_score_rows:
        admin.table("eval_scores").insert(eval_score_rows).execute()
        completed_ids = [r["trace_id"] for r in eval_score_rows]
        for chunk_start in range(0, len(completed_ids), 200):
            admin.table("traces").update({"eval_status": "completed"}).in_(
                "id", completed_ids[chunk_start:chunk_start + 200]
            ).execute()

    for status, ids in status_updates.items():
        if not ids:
            continue
        update = {"eval_status": status}
        if status == "failed":
            update["eval_attempts"] = 3
        admin.table("traces").update(update).in_("id", ids).execute()

    return {
        "traces": len(trace_ids),
        "completed": len(eval_score_rows),
        "pending": len(status_updates["pending"]),
        "skipped": len(status_updates["skipped"]),
        "failed": len(status_updates["failed"]),
    }


def seed_health_history(admin, pipeline_id: str, days: int, trend: str, now: datetime) -> None:
    rows = []
    for d in range(days, 0, -1):
        if trend == "stable":
            score = RNG.uniform(88, 96)
        elif trend == "degrading":
            score = RNG.uniform(88, 95) if d > 4 else RNG.uniform(45, 65)
        else:  # improving
            progress = 1 - (d / days)
            score = 65 + progress * 28 + RNG.uniform(-3, 3)
        score = round(max(0, min(100, score)), 1)
        halluc = RNG.randint(0, 1) if trend != "degrading" or d > 4 else RNG.randint(3, 8)
        rows.append({
            "pipeline_id": pipeline_id,
            "date": (now - timedelta(days=d)).date().isoformat(),
            "health_score": score,
            "avg_faithfulness": round(score / 100, 3),
            "avg_answer_relevance": round(min(1, score / 100 + 0.02), 3),
            "avg_context_precision": round(max(0, score / 100 - 0.05), 3),
            "eval_count": RNG.randint(15, 60),
            "hallucination_count": halluc,
        })
    admin.table("pipeline_health_daily").insert(rows).execute()


def main() -> int:
    admin = load_admin()

    users = admin.auth.admin.list_users()
    demo_user = next((u for u in users if u.email == DEMO_EMAIL), None)
    if demo_user is None:
        print(f"FATAL: {DEMO_EMAIL} not found — create it first.")
        return 1
    user_id = demo_user.id
    print(f"Seeding for {DEMO_EMAIL} ({user_id})")

    # Wipe any previously seeded data for this user (idempotent re-run).
    admin.table("alerts").delete().eq("user_id", user_id).execute()
    existing_pipelines = admin.table("pipelines").select("id").eq("user_id", user_id).execute().data
    if existing_pipelines:
        admin.table("pipelines").delete().eq("user_id", user_id).execute()
        print(f"Cleared {len(existing_pipelines)} previously seeded pipeline(s)")

    now = datetime.now(timezone.utc)

    pipeline_specs = [
        {"name": "support-bot-prod", "framework": "langchain", "eval_sample_rate": 1.0, "n_traces": 70, "days": 30, "trend": "stable"},
        {"name": "docs-search-api", "framework": "llamaindex", "eval_sample_rate": 0.5, "n_traces": 65, "days": 30, "trend": "degrading"},
        {"name": "internal-kb-assistant", "framework": "custom", "eval_sample_rate": 1.0, "n_traces": 50, "days": 21, "trend": "improving"},
    ]

    summary = []
    pipeline_ids = {}
    for spec in pipeline_specs:
        pipeline = admin.table("pipelines").insert({
            "user_id": user_id,
            "name": spec["name"],
            "framework": spec["framework"],
            "eval_sample_rate": spec["eval_sample_rate"],
        }).execute().data[0]
        pid = pipeline["id"]
        pipeline_ids[spec["name"]] = pid

        stats = seed_pipeline_traces(admin, pid, user_id, spec["n_traces"], spec["days"], now)
        seed_health_history(admin, pid, spec["days"], spec["trend"], now)
        summary.append((spec["name"], spec["framework"], stats))
        print(f"  {spec['name']} ({spec['framework']}): {stats}")

    # Active alerts on the degrading pipeline
    degrading_id = pipeline_ids["docs-search-api"]
    admin.table("alerts").insert([
        {
            "user_id": user_id, "pipeline_id": degrading_id,
            "alert_type": "faithfulness_drop", "severity": "critical",
            "message": "Faithfulness dropped 42% vs the 7-day baseline (0.91 -> 0.53 over the last 24h).",
            "metric_before": 0.91, "metric_after": 0.53,
        },
        {
            "user_id": user_id, "pipeline_id": degrading_id,
            "alert_type": "hallucination_spike", "severity": "critical",
            "message": "Hallucination rate spiked to 28% over the last 24h (baseline 3%).",
            "metric_before": 0.03, "metric_after": 0.28,
        },
    ]).execute()

    # One resolved historical alert on the stable pipeline, for alert-history realism
    stable_id = pipeline_ids["support-bot-prod"]
    admin.table("alerts").insert({
        "user_id": user_id, "pipeline_id": stable_id,
        "alert_type": "faithfulness_drop", "severity": "warning",
        "message": "Faithfulness dropped 18% vs the 7-day baseline (0.94 -> 0.77 over the last 24h).",
        "metric_before": 0.94, "metric_after": 0.77, "resolved": True,
    }).execute()

    # The dashboard's "traces this month" stat reads the `usage` table, which
    # is normally incremented by the real consume_trace_quota RPC on ingest.
    # Traces were inserted directly here (not through the API), so backfill
    # it to match what actually landed in the current calendar month.
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    all_pids = list(pipeline_ids.values())
    this_month_traces = (
        admin.table("traces")
        .select("id", count="exact")
        .in_("pipeline_id", all_pids)
        .gte("created_at", month_start.isoformat())
        .execute()
    )
    admin.table("usage").upsert(
        {
            "user_id": user_id,
            "month": month_start.date().isoformat(),
            "traces_count": this_month_traces.count or 0,
        },
        on_conflict="user_id,month",
    ).execute()

    total_traces = sum(s[2]["traces"] for s in summary)
    print(f"\nDone. {len(pipeline_specs)} pipelines, {total_traces} traces, 3 alerts (2 active critical, 1 resolved).")
    print(f"Backfilled usage.traces_count={this_month_traces.count} for {month_start.date().isoformat()}")
    print("Log in as demo@kairos.local and refresh the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
