from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    content: str
    score: float
    doc_id: str
    metadata: dict[str, Any] | None = None


class TraceIngestRequest(BaseModel):
    pipeline_id: UUID
    query: str = Field(min_length=1)
    retrieved_chunks: list[RetrievedChunk] = Field(min_length=1)
    reranked_chunks: list[RetrievedChunk] | None = None
    final_answer: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    token_count_input: int | None = Field(default=None, ge=0)
    token_count_output: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = None
    metadata: dict[str, Any] | None = None


class TraceIngestResponse(BaseModel):
    trace_id: UUID
    status: Literal["queued"] = "queued"


class TraceBatchIngestRequest(BaseModel):
    traces: list[TraceIngestRequest] = Field(min_length=1, max_length=100)


class TraceBatchIngestResponse(BaseModel):
    trace_ids: list[UUID]
    status: Literal["queued"] = "queued"
