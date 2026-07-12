"""Pydantic models the SDK sends to the Kairos API. Mirrors
apps/api/models/trace.py's request shape — keep the two in sync."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    content: str
    score: float
    doc_id: str
    metadata: dict[str, Any] | None = None


class TracePayload(BaseModel):
    pipeline_id: str
    query: str
    retrieved_chunks: list[RetrievedChunk]
    reranked_chunks: list[RetrievedChunk] | None = None
    final_answer: str
    latency_ms: int
    token_count_input: int | None = None
    token_count_output: int | None = None
    estimated_cost_usd: Decimal | None = None
    metadata: dict[str, Any] | None = None
