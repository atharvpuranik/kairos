"""LangChain callback handler. Requires the `langchain` extra:
    pip install kairos-ai[langchain]

Captures one complete trace per chain run: query (chain input), retrieved
documents (on_retriever_end), final answer + token usage (on_llm_end /
on_chain_end), and wall-clock latency across the run.

IMPORTANT: pass the handler at invoke time, not at chain-construction time —
    chain = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)
    chain.invoke({"query": user_query}, config={"callbacks": [handler]})
Chain-constructor callbacks (`RetrievalQA.from_chain_type(..., callbacks=[handler])`)
do not propagate to the retriever/LLM sub-runs in current LangChain versions,
so on_retriever_end/on_llm_end never fire and no chunks would be captured.

RetrievalQA is composed of nested chains (outer QA chain -> combine-documents
chain -> LLM chain), each of which fires its own on_chain_start/on_chain_end.
Only the outermost run (parent_run_id is None) is treated as the trace
boundary; nested runs are ignored so they can't reset or prematurely close it.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "kairos.integrations.langchain requires langchain-core. "
        "Install with: pip install kairos-ai[langchain]"
    ) from exc

from kairos.client import KairosClient
from kairos.models import RetrievedChunk, TracePayload


class KairosCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        api_key: str | None = None,
        pipeline_id: str | None = None,
        api_url: str | None = None,
    ) -> None:
        super().__init__()
        self._client = KairosClient(api_key=api_key, api_url=api_url)
        self._pipeline_id = pipeline_id
        self._reset()

    def _reset(self) -> None:
        self._query: str | None = None
        self._start: float | None = None
        self._chunks: list[RetrievedChunk] = []
        self._token_input: int | None = None
        self._token_output: int | None = None

    def on_chain_start(self, serialized: dict[str, Any], inputs: dict[str, Any], **kwargs: Any) -> None:
        if kwargs.get("parent_run_id") is not None:
            return  # nested sub-chain (e.g. combine-documents, LLM chain) — not the trace boundary
        self._reset()
        self._start = time.perf_counter()
        if isinstance(inputs, dict):
            self._query = (
                inputs.get("query")
                or inputs.get("question")
                or (next(iter(inputs.values())) if inputs else "")
            )
        else:
            self._query = str(inputs)

    def on_retriever_end(self, documents: list[Any], **kwargs: Any) -> None:
        for i, doc in enumerate(documents):
            metadata = getattr(doc, "metadata", {}) or {}
            self._chunks.append(
                RetrievedChunk(
                    content=getattr(doc, "page_content", str(doc)),
                    score=float(metadata.get("score", 0.0)),
                    doc_id=str(metadata.get("doc_id") or metadata.get("source") or i),
                    metadata=metadata or None,
                )
            )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            usage = (response.llm_output or {}).get("token_usage", {})
            self._token_input = usage.get("prompt_tokens")
            self._token_output = usage.get("completion_tokens")
        except Exception:
            pass

    def on_chain_end(self, outputs: dict[str, Any], **kwargs: Any) -> None:
        if kwargs.get("parent_run_id") is not None:
            return  # nested sub-chain ending — wait for the outermost run
        if self._start is None or not self._query or not self._chunks:
            return
        answer = None
        if isinstance(outputs, dict):
            answer = (
                outputs.get("result")
                or outputs.get("answer")
                or outputs.get("output_text")
                or (next(iter(outputs.values())) if outputs else None)
            )
        elif isinstance(outputs, str):
            answer = outputs
        if not answer:
            return

        latency_ms = int((time.perf_counter() - self._start) * 1000)
        payload = TracePayload(
            pipeline_id=self._pipeline_id,
            query=self._query,
            retrieved_chunks=self._chunks,
            final_answer=str(answer),
            latency_ms=latency_ms,
            token_count_input=self._token_input,
            token_count_output=self._token_output,
        )
        self._client.send_trace(payload)
