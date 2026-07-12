"""Generic wrapper for any retriever — normalizes different retriever
calling conventions and return shapes so KairosTracer.wrap() works
regardless of framework, without hard-depending on any of them."""

from __future__ import annotations

from typing import Any


def call_retriever(retriever: Any, query: str, *args: Any, **kwargs: Any) -> Any:
    """Calls `retriever` with `query` using whichever convention it exposes:
    a plain callable, or an object with .retrieve()/.get_relevant_documents()/.invoke()."""
    if callable(retriever):
        return retriever(query, *args, **kwargs)
    for method_name in ("retrieve", "get_relevant_documents", "invoke"):
        method = getattr(retriever, method_name, None)
        if callable(method):
            return method(query, *args, **kwargs)
    raise TypeError(
        f"Kairos: don't know how to call retriever of type {type(retriever).__name__}. "
        "Expected a callable, or an object with .retrieve()/.get_relevant_documents()/.invoke()."
    )


def normalize_chunks(raw_result: Any) -> list[dict[str, Any]]:
    """Normalizes a retriever's raw return value into Kairos chunk dicts:
    [{content, score, doc_id, metadata}]. Handles dicts, LangChain-style
    Document objects (.page_content/.metadata), (content, score) tuples,
    and plain strings."""
    items = raw_result if isinstance(raw_result, (list, tuple)) else [raw_result]
    chunks: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            chunks.append(
                {
                    "content": item.get("content", ""),
                    "score": float(item.get("score", 0.0)),
                    "doc_id": str(item.get("doc_id", i)),
                    "metadata": item.get("metadata"),
                }
            )
        elif hasattr(item, "page_content"):
            metadata = getattr(item, "metadata", {}) or {}
            chunks.append(
                {
                    "content": item.page_content,
                    "score": float(metadata.get("score", 0.0)),
                    "doc_id": str(metadata.get("doc_id") or metadata.get("source") or i),
                    "metadata": metadata or None,
                }
            )
        elif isinstance(item, tuple) and len(item) == 2:
            content, score = item
            chunks.append(
                {"content": str(content), "score": float(score), "doc_id": str(i), "metadata": None}
            )
        else:
            chunks.append({"content": str(item), "score": 0.0, "doc_id": str(i), "metadata": None})
    return chunks
