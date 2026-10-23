"""Bound blocking probes without letting HTTP cancellation multiply worker threads."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore


class ProbeRunner:
    def __init__(self, max_workers: int = 4) -> None:
        self._slots = BoundedSemaphore(max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="readiness"
        )

    async def run(self, probe: Callable[[], None]) -> None:
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("Readiness probe capacity exhausted")
        try:
            context = copy_context()
            future = self._executor.submit(context.run, probe)
        except Exception:
            self._slots.release()
            raise
        future.add_done_callback(self._release_slot)
        # Cancelling this await returns immediately. A running thread continues,
        # and retains its capacity slot until the actual blocking call completes.
        await asyncio.wrap_future(future)

    def _release_slot(self, future: Future[None]) -> None:
        self._slots.release()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
