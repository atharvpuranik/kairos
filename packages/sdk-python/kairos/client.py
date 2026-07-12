"""Async HTTP client to the Kairos API.

Batching + fire-and-forget by design, per ARCHITECTURE.md's non-blocking SDK
principle:

- send_trace() appends to an in-memory buffer and returns immediately.
- A background flusher POSTs the buffer to /v1/traces/batch every
  `flush_interval` seconds, or immediately once `flush_at` traces accumulate.
- The buffer is bounded (`max_buffer_size`); when full, new traces are dropped
  with a warning — tracing must never block or grow memory unboundedly.
- Network/API errors are logged, never raised into the caller's code, and the
  failed batch is dropped (observability data, not business data).
- flush() forces a synchronous drain (used at shutdown; also registered via
  atexit so short-lived scripts don't silently lose their last traces).
"""

from __future__ import annotations

import atexit
import logging
import os
import threading

import httpx

from kairos.models import TracePayload
from kairos.utils import BackgroundDispatcher

logger = logging.getLogger("kairos")

DEFAULT_API_URL = "https://api.kairos.dev"
DEFAULT_FLUSH_INTERVAL = 2.0
DEFAULT_FLUSH_AT = 20
DEFAULT_MAX_BUFFER_SIZE = 1000


class KairosClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        timeout: float = 10.0,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        flush_at: int = DEFAULT_FLUSH_AT,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ) -> None:
        self.api_key = api_key or os.environ.get("KAIROS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Kairos API key required: pass api_key=... or set the KAIROS_API_KEY env var"
            )
        self.api_url = (api_url or os.environ.get("KAIROS_API_URL") or DEFAULT_API_URL).rstrip("/")
        self._timeout = timeout
        self._flush_interval = flush_interval
        self._flush_at = flush_at
        self._max_buffer_size = max_buffer_size

        self._buffer: list[TracePayload] = []
        self._buffer_lock = threading.Lock()
        self._dropped_count = 0

        self._dispatcher = BackgroundDispatcher()
        self._http: httpx.AsyncClient | None = None  # created lazily on the loop thread
        self._dispatcher.dispatch(self._periodic_flush())
        atexit.register(self._shutdown_flush)

    # -- public API ---------------------------------------------------------

    def send_trace(self, payload: TracePayload) -> None:
        """Buffers the trace and returns immediately — never blocks, never raises."""
        flush_now = False
        with self._buffer_lock:
            if len(self._buffer) >= self._max_buffer_size:
                self._dropped_count += 1
                if self._dropped_count == 1 or self._dropped_count % 100 == 0:
                    logger.warning(
                        "Kairos: trace buffer full (%d), dropping traces (%d dropped so far)",
                        self._max_buffer_size,
                        self._dropped_count,
                    )
                return
            self._buffer.append(payload)
            if len(self._buffer) >= self._flush_at:
                flush_now = True
        if flush_now:
            self._dispatcher.dispatch(self._flush())

    def flush(self, timeout: float = 10.0) -> None:
        """Drains the buffer synchronously. Safe to call from any thread."""
        future = self._dispatcher.dispatch(self._flush())
        try:
            future.result(timeout=timeout)
        except Exception as exc:  # never raise into caller code
            logger.warning("Kairos: flush failed: %s", exc)

    # -- internals ----------------------------------------------------------

    def _drain(self) -> list[TracePayload]:
        with self._buffer_lock:
            batch, self._buffer = self._buffer, []
        return batch

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._http

    async def _flush(self) -> None:
        batch = self._drain()
        if not batch:
            return
        try:
            http = await self._get_http()
            response = await http.post(
                f"{self.api_url}/v1/traces/batch",
                json={"traces": [p.model_dump(mode="json", exclude_none=True) for p in batch]},
            )
            if response.status_code >= 400:
                logger.warning(
                    "Kairos: batch ingest failed (%s): %s — %d trace(s) dropped",
                    response.status_code,
                    response.text,
                    len(batch),
                )
        except httpx.HTTPError as exc:
            logger.warning("Kairos: batch ingest failed: %s — %d trace(s) dropped", exc, len(batch))

    async def _periodic_flush(self) -> None:
        import asyncio

        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    def _shutdown_flush(self) -> None:
        try:
            self.flush(timeout=3.0)
        except Exception:
            pass
