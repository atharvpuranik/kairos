"""Eval computation — ARCHITECTURE.md section 10.

One structured-output call scores faithfulness, answer_relevance, and
context_precision together (cheaper and simpler than three separate calls,
while still measuring the same three metrics against the same inputs the
architecture doc describes). hallucination_flag/detail and failure_category
come back in the same response; compute_eval() then enforces the documented
derivation rule for hallucination_flag as a deterministic safety net over
the model's own judgment.

Provider is Claude Haiku by default (the documented spec). Set
EVAL_PROVIDER=gemini to use Gemini instead — a dev/test stand-in while the
Anthropic account has no credit, not a change to the production default.
"""

from __future__ import annotations

from functools import lru_cache

import anthropic
from google import genai
from google.genai import types as genai_types

from config import get_settings
from models.eval import EvalResult

MODEL_ID = "claude-haiku-4-5"

# Dev/test stand-in while the Anthropic account has no credit (see EVAL_PROVIDER).
# Not the documented default — ARCHITECTURE.md section 10 specifies Claude Haiku.
GEMINI_MODEL_ID = "gemini-flash-lite-latest"

# Bump whenever EVAL_SYSTEM_PROMPT (or the scoring semantics) change — stamped
# onto every eval_scores row so historical scores remain comparable.
PROMPT_VERSION = "v1"

EVAL_SYSTEM_PROMPT = """You are an expert RAG (Retrieval-Augmented Generation) evaluator. \
Given a user's query, the chunks retrieved by the RAG pipeline, and the final answer \
generated, score the pipeline on three dimensions and diagnose any failure.

- faithfulness (0.0-1.0): Does the final answer ONLY contain information present in the \
retrieved chunks? 1.0 = every claim is grounded in the chunks. Lower scores mean the \
answer contains unsupported or fabricated claims.
- answer_relevance (0.0-1.0): Does the final answer actually address what the user asked? \
1.0 = fully addresses the query. Lower scores mean the answer is off-topic or incomplete.
- context_precision (0.0-1.0): What fraction of the retrieved chunks actually contributed \
useful information to the answer? 1.0 = every chunk was relevant and used.

Hallucination: if faithfulness is low AND the answer contains specific claims not \
supported by any retrieved chunk, set hallucination_flag=true and hallucination_detail \
to a short description of exactly what was hallucinated (quote or paraphrase the \
unsupported claim). Otherwise hallucination_flag=false and hallucination_detail=null.

Failure category: if faithfulness, answer_relevance, or context_precision is below 0.7, \
you MUST set failure_category to the single best root cause and failure_reason to a \
short human-readable explanation. Categories:
- chunking: chunks were too large/small or split mid-context, losing needed information
- embedding: semantically wrong chunks were retrieved for this query
- reranking: the right chunks were retrieved but ranked poorly / not surfaced
- prompt: the context given to the model was adequate but the prompt caused information loss
- model: the model ignored the provided context and answered from its own training knowledge
If all three scores are >= 0.7, set failure_category=null and failure_reason=null."""


@lru_cache
def _get_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


@lru_cache
def _get_gemini_client() -> genai.Client:
    settings = get_settings()
    # The Gemini free tier is rate-limited far tighter than Claude's paid tier
    # (observed: 5 requests/min on gemini-flash-latest) — retry with backoff
    # on 429 rather than surfacing a transient rate limit as a hard failure.
    retry_options = genai_types.HttpRetryOptions(
        attempts=8,
        initial_delay=15.0,
        max_delay=70.0,
        exp_base=1.8,
        http_status_codes=[429],
    )
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=genai_types.HttpOptions(retry_options=retry_options),
    )


def _build_user_prompt(query: str, retrieved_chunks: list[dict], final_answer: str) -> str:
    chunks_text = "\n\n".join(
        f"[chunk {i} | doc_id={c.get('doc_id')}]\n{c.get('content', '')}"
        for i, c in enumerate(retrieved_chunks)
    )
    return (
        f"Query: {query}\n\n"
        f"Retrieved chunks:\n{chunks_text}\n\n"
        f"Final answer given to the user:\n{final_answer}"
    )


async def _compute_eval_anthropic(query: str, retrieved_chunks: list[dict], final_answer: str) -> EvalResult:
    response = await _get_client().messages.parse(
        model=MODEL_ID,
        max_tokens=1024,
        system=EVAL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(query, retrieved_chunks, final_answer)}],
        output_format=EvalResult,
    )
    return response.parsed_output


async def _compute_eval_gemini(query: str, retrieved_chunks: list[dict], final_answer: str) -> EvalResult:
    response = await _get_gemini_client().aio.models.generate_content(
        model=GEMINI_MODEL_ID,
        contents=_build_user_prompt(query, retrieved_chunks, final_answer),
        config=genai_types.GenerateContentConfig(
            system_instruction=EVAL_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=EvalResult,
        ),
    )
    return EvalResult.model_validate_json(response.text)


async def compute_eval(query: str, retrieved_chunks: list[dict], final_answer: str) -> EvalResult:
    settings = get_settings()
    if settings.eval_provider == "gemini":
        result = await _compute_eval_gemini(query, retrieved_chunks, final_answer)
    else:
        result = await _compute_eval_anthropic(query, retrieved_chunks, final_answer)

    # Deterministic safety net for the documented derivation rule (section 10):
    # hallucination_flag requires faithfulness < 0.6, regardless of the model's own flag.
    if result.faithfulness >= 0.6:
        result = result.model_copy(update={"hallucination_flag": False, "hallucination_detail": None})

    return result
