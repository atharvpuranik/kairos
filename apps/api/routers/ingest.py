import time

from fastapi import APIRouter, Depends, HTTPException, status

from config import get_settings
from db.redis_client import get_redis
from models.trace import (
    TraceBatchIngestRequest,
    TraceBatchIngestResponse,
    TraceIngestRequest,
    TraceIngestResponse,
)
from models.user import AuthenticatedKey
from routers.auth import get_current_api_key
from services.trace_service import insert_trace, insert_traces

router = APIRouter(prefix="/v1", tags=["ingest"])


async def rate_limited_api_key(
    auth: AuthenticatedKey = Depends(get_current_api_key),
) -> AuthenticatedKey:
    """Fixed-window per-key rate limit (Upstash INCR). One batch request
    counts as one request; the monthly usage quota counts individual traces."""
    settings = get_settings()
    redis = get_redis()
    window = int(time.time() // 60)
    counter_key = f"rl:{auth.api_key_id}:{window}"

    count = await redis.incr(counter_key)
    if count == 1:
        await redis.expire(counter_key, 120)
    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({settings.rate_limit_per_minute} requests/minute per key)",
        )
    return auth


@router.post(
    "/traces",
    response_model=TraceIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_trace(
    request: TraceIngestRequest,
    auth: AuthenticatedKey = Depends(rate_limited_api_key),
) -> TraceIngestResponse:
    trace_id = await insert_trace(request, user_id=auth.user_id)
    return TraceIngestResponse(trace_id=trace_id, status="queued")


@router.post(
    "/traces/batch",
    response_model=TraceBatchIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_trace_batch(
    request: TraceBatchIngestRequest,
    auth: AuthenticatedKey = Depends(rate_limited_api_key),
) -> TraceBatchIngestResponse:
    trace_ids = await insert_traces(request.traces, user_id=auth.user_id)
    return TraceBatchIngestResponse(trace_ids=trace_ids, status="queued")
