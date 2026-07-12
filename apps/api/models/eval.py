from typing import Literal

from pydantic import BaseModel, Field

FailureCategory = Literal["chunking", "embedding", "reranking", "prompt", "model"]


class EvalResult(BaseModel):
    """Claude Haiku's structured judgment for one trace. Mirrors the scored
    columns on eval_scores (ARCHITECTURE.md section 7)."""

    faithfulness: float = Field(ge=0, le=1)
    answer_relevance: float = Field(ge=0, le=1)
    context_precision: float = Field(ge=0, le=1)
    hallucination_flag: bool
    hallucination_detail: str | None = None
    failure_category: FailureCategory | None = None
    failure_reason: str | None = None
