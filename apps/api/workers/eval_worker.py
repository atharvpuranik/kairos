"""Background eval job processor — ARCHITECTURE.md v1.1 section 11.

Status-driven queue on the traces table (no Inngest/Redis queue — see the
docker-compose.yml `worker` service this matches):

- traces are written by the API with eval_status='pending'
- the worker polls only pending traces (partial index makes this cheap),
  applies the pipeline's eval_sample_rate and the per-user daily eval cap,
  scores via Claude Haiku, and transitions the trace to
  'completed' | 'skipped' (sampled out / over cap) | 'failed' (max attempts)
- eval_scores has UNIQUE(trace_id), so a race can never double-score
- chunk_index updates go through the atomic upsert_chunk_retrieval RPC

Run standalone:
    python -m workers.eval_worker
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

from config import get_settings
from db.redis_client import get_redis
from db.supabase import get_supabase
from services.alerting import check_pipeline_alerts
from services.eval_service import MODEL_ID, PROMPT_VERSION, compute_eval

logger = logging.getLogger("kairos.eval_worker")

POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 10
FAILURE_THRESHOLD = 0.7


async def fetch_pending_traces(supabase, limit: int) -> list[dict]:
    result = (
        await supabase.table("traces")
        .select("*, pipelines(eval_sample_rate)")
        .eq("eval_status", "pending")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data


async def _set_status(supabase, trace_id: str, status: str, attempts: int | None = None) -> None:
    update: dict = {"eval_status": status}
    if attempts is not None:
        update["eval_attempts"] = attempts
    await supabase.table("traces").update(update).eq("id", trace_id).execute()


async def _update_chunk_index(supabase, pipeline_id: str, chunks: list[dict]) -> None:
    for chunk in chunks:
        chunk_id = chunk.get("doc_id")
        if not chunk_id:
            continue
        metadata = chunk.get("metadata") or {}
        await supabase.rpc(
            "upsert_chunk_retrieval",
            {
                "p_pipeline_id": pipeline_id,
                "p_chunk_id": chunk_id,
                "p_preview": (chunk.get("content") or "")[:200],
                "p_source": metadata.get("source"),
            },
        ).execute()


async def _over_daily_cap(user_id: str) -> bool:
    settings = get_settings()
    redis = get_redis()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    counter_key = f"evals:{user_id}:{today}"
    count = await redis.incr(counter_key)
    if count == 1:
        await redis.expire(counter_key, 90_000)  # ~25h, outlives the UTC day
    return count > settings.eval_daily_cap_per_user


def _sample_rate(trace: dict) -> float:
    pipeline = trace.get("pipelines") or {}
    try:
        return float(pipeline.get("eval_sample_rate", 1.0))
    except (TypeError, ValueError):
        return 1.0


async def process_trace(supabase, trace: dict) -> None:
    """Scores one pending trace and transitions its eval_status. Raises on
    eval/db errors — the caller owns attempt counting."""
    sample_rate = _sample_rate(trace)
    if sample_rate < 1.0 and random.random() >= sample_rate:
        await _set_status(supabase, trace["id"], "skipped")
        return

    if await _over_daily_cap(trace["user_id"]):
        logger.warning("user %s over daily eval cap; skipping trace %s", trace["user_id"], trace["id"])
        await _set_status(supabase, trace["id"], "skipped")
        return

    result = await compute_eval(
        query=trace["query"],
        retrieved_chunks=trace["retrieved_chunks"],
        final_answer=trace["final_answer"],
    )

    lowest = min(result.faithfulness, result.answer_relevance, result.context_precision)
    if lowest < FAILURE_THRESHOLD and result.failure_category is None:
        logger.warning(
            "trace %s scored below threshold (%.2f) but no failure_category was set",
            trace["id"],
            lowest,
        )

    await supabase.table("eval_scores").insert(
        {
            "trace_id": trace["id"],
            "pipeline_id": trace["pipeline_id"],
            "faithfulness": result.faithfulness,
            "answer_relevance": result.answer_relevance,
            "context_precision": result.context_precision,
            "hallucination_flag": result.hallucination_flag,
            "hallucination_detail": result.hallucination_detail,
            "failure_reason": result.failure_reason,
            "failure_category": result.failure_category,
            "model_used": MODEL_ID,
            "prompt_version": PROMPT_VERSION,
        }
    ).execute()

    await _update_chunk_index(supabase, trace["pipeline_id"], trace["retrieved_chunks"])
    await _set_status(supabase, trace["id"], "completed")

    try:
        await check_pipeline_alerts(supabase, trace["pipeline_id"], trace["user_id"])
    except Exception:
        # alerting must never break scoring
        logger.exception("alert check failed for pipeline %s", trace["pipeline_id"])


async def process_once() -> int:
    settings = get_settings()
    supabase = await get_supabase()
    traces = await fetch_pending_traces(supabase, BATCH_SIZE)
    for trace in traces:
        try:
            await process_trace(supabase, trace)
        except Exception:
            attempts = trace.get("eval_attempts", 0) + 1
            status = "failed" if attempts >= settings.eval_max_attempts else "pending"
            logger.exception(
                "failed to process trace %s (attempt %d/%d -> %s)",
                trace["id"],
                attempts,
                settings.eval_max_attempts,
                status,
            )
            try:
                await _set_status(supabase, trace["id"], status, attempts=attempts)
            except Exception:
                logger.exception("failed to record attempt for trace %s", trace["id"])
    return len(traces)


async def run_forever() -> None:
    logger.info("eval worker starting")
    while True:
        try:
            n = await process_once()
            if n == 0:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception:
            logger.exception("eval worker cycle failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
