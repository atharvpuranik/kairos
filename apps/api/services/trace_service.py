from uuid import UUID

from fastapi import HTTPException, status

from config import get_settings
from db.supabase import get_supabase
from models.trace import TraceIngestRequest


def _trace_row(request: TraceIngestRequest, user_id: UUID) -> dict:
    return {
        "pipeline_id": str(request.pipeline_id),
        "user_id": str(user_id),
        "query": request.query,
        "retrieved_chunks": [chunk.model_dump() for chunk in request.retrieved_chunks],
        "reranked_chunks": (
            [chunk.model_dump() for chunk in request.reranked_chunks]
            if request.reranked_chunks is not None
            else None
        ),
        "final_answer": request.final_answer,
        "latency_ms": request.latency_ms,
        "token_count_input": request.token_count_input,
        "token_count_output": request.token_count_output,
        "estimated_cost_usd": (
            str(request.estimated_cost_usd) if request.estimated_cost_usd is not None else None
        ),
        "metadata": request.metadata,
    }


async def _validate_pipelines(supabase, pipeline_ids: set[UUID], user_id: UUID) -> None:
    result = (
        await supabase.table("pipelines")
        .select("id")
        .eq("user_id", str(user_id))
        .in_("id", [str(p) for p in pipeline_ids])
        .execute()
    )
    owned = {row["id"] for row in result.data}
    missing = [str(p) for p in pipeline_ids if str(p) not in owned]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline(s) not found: {', '.join(missing)}",
        )


async def _consume_quota(supabase, user_id: UUID, count: int) -> None:
    settings = get_settings()
    result = await supabase.rpc(
        "consume_trace_quota",
        {
            "p_user_id": str(user_id),
            "p_count": count,
            "p_limit": settings.free_tier_traces_per_month,
        },
    ).execute()
    if result.data is not True:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Monthly trace limit reached ({settings.free_tier_traces_per_month}/month "
                "on the free tier). Traces beyond the limit are rejected."
            ),
        )


async def insert_traces(requests: list[TraceIngestRequest], user_id: UUID) -> list[UUID]:
    """Validates pipeline ownership + monthly quota, then writes the traces.

    Traces are written with eval_status='pending'; the background eval worker
    picks them up from there (Week 3+; Inngest deliberately not used — see
    ARCHITECTURE.md v1.1 section 11).
    """
    supabase = await get_supabase()

    await _validate_pipelines(supabase, {r.pipeline_id for r in requests}, user_id)
    await _consume_quota(supabase, user_id, len(requests))

    rows = [_trace_row(r, user_id) for r in requests]
    result = await supabase.table("traces").insert(rows).execute()
    return [UUID(row["id"]) for row in result.data]


async def insert_trace(request: TraceIngestRequest, user_id: UUID) -> UUID:
    return (await insert_traces([request], user_id))[0]
