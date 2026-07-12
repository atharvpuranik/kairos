"""Background dispatcher: runs fire-and-forget coroutines on a dedicated
event loop thread, so tracing never blocks the caller's (sync or async)
code and never depends on the host application running its own loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine
from typing import Any


class BackgroundDispatcher:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def dispatch(self, coro: Coroutine[Any, Any, None]) -> concurrent.futures.Future:
        """Schedules coro on the background loop and returns immediately.
        Callers doing fire-and-forget tracing ignore the returned future;
        tests can call .result(timeout=...) on it to synchronize."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)
