"""KairosTracer — RAG wrapping.

Two ways to trace, per ARCHITECTURE.md section 9:

1. Manual (always produces a complete trace):
    with tracer.trace(query=user_query) as t:
        chunks = your_retriever.retrieve(user_query)
        t.log_retrieval(chunks)
        answer = your_llm.generate(chunks, user_query)
        t.log_answer(answer)

2. tracer.wrap(retriever) instruments a retriever so that any call made
   while a `tracer.trace(...)` context is active on the same thread/task
   automatically logs its result via log_retrieval() — one less line to
   write in the common case. A trace is only ever submitted with a
   final_answer attached (the API requires it), so wrap() calls made
   outside an active trace context just pass through untraced.
"""

from __future__ import annotations

import contextvars
import os
import time
from types import TracebackType
from typing import Any, Callable

from kairos.client import KairosClient
from kairos.integrations.custom import call_retriever, normalize_chunks
from kairos.models import RetrievedChunk, TracePayload

_active_trace: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "kairos_active_trace", default=None
)


class TraceContext:
    def __init__(self, tracer: "KairosTracer", query: str, pipeline_id: str) -> None:
        self._tracer = tracer
        self._query = query
        self._pipeline_id = pipeline_id
        self._chunks: list[RetrievedChunk] = []
        self._reranked_chunks: list[RetrievedChunk] | None = None
        self._final_answer: str | None = None
        self._metadata: dict[str, Any] | None = None
        self._start = time.perf_counter()
        self._token: contextvars.Token | None = None

    def log_retrieval(
        self, chunks: list[dict[str, Any]] | list[RetrievedChunk], reranked: bool = False
    ) -> None:
        parsed = [c if isinstance(c, RetrievedChunk) else RetrievedChunk(**c) for c in chunks]
        if reranked:
            self._reranked_chunks = parsed
        else:
            self._chunks = parsed

    def log_answer(self, answer: str) -> None:
        self._final_answer = answer

    def set_metadata(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata

    def __enter__(self) -> "TraceContext":
        self._token = _active_trace.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._token is not None
        _active_trace.reset(self._token)
        if exc is not None:
            return
        if not self._chunks or self._final_answer is None:
            return
        latency_ms = int((time.perf_counter() - self._start) * 1000)
        payload = TracePayload(
            pipeline_id=self._pipeline_id,
            query=self._query,
            retrieved_chunks=self._chunks,
            reranked_chunks=self._reranked_chunks,
            final_answer=self._final_answer,
            latency_ms=latency_ms,
            metadata=self._metadata,
        )
        self._tracer.client.send_trace(payload)


class KairosTracer:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        pipeline_id: str | None = None,
    ) -> None:
        self.client = KairosClient(api_key=api_key, api_url=api_url)
        self.pipeline_id = pipeline_id or os.environ.get("KAIROS_PIPELINE_ID")

    def trace(self, query: str, pipeline_id: str | None = None) -> TraceContext:
        resolved_pipeline_id = pipeline_id or self.pipeline_id
        if not resolved_pipeline_id:
            raise ValueError(
                "pipeline_id required: pass it to trace()/KairosTracer(...) "
                "or set the KAIROS_PIPELINE_ID env var"
            )
        return TraceContext(self, query=query, pipeline_id=resolved_pipeline_id)

    def wrap(self, retriever: Any) -> Callable[..., Any]:
        def wrapped(query: str, *args: Any, **kwargs: Any) -> Any:
            result = call_retriever(retriever, query, *args, **kwargs)
            active = _active_trace.get()
            if active is not None:
                active.log_retrieval(normalize_chunks(result))
            return result

        return wrapped
