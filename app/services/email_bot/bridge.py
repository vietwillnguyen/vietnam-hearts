"""The one place a synchronous pipeline reaches an ``async def``.

``POST /admin/email-bot/poll`` is a plain ``def`` route, so FastAPI runs it in
the threadpool and the synchronous Gmail, classifier and Supabase clients never
block the event loop. That makes ``EmailBotPipeline.run()`` synchronous, but
``BotService.chat()`` is a coroutine, so one bridge is unavoidable.

``anyio.from_thread.run`` is the right bridge and ``asyncio.run`` is not: the
former hands the coroutine back to the event loop that owns this worker thread,
so the loop's task group, cancellation and any loop-bound client stay intact.
``asyncio.run`` would instead create a second event loop inside a worker thread
of the first, which is how loop-bound resources start raising "attached to a
different loop" under load.

The fallback exists only for the case where there is no loop to hand anything
back to - a CLI script or an eval runner calling the pipeline directly - which
is exactly where ``asyncio.run`` is the correct thing to do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


def in_anyio_worker_thread() -> bool:
    """Whether this thread was spawned by ``anyio.to_thread.run_sync``.

    Probed with a no-op ``run_sync`` rather than by reading anyio's
    thread-locals, so the answer comes from the same machinery the real call
    uses and cannot be confused by a ``RuntimeError`` raised inside the
    caller's own coroutine.
    """
    import anyio.from_thread

    try:
        anyio.from_thread.run_sync(lambda: None)
    except RuntimeError:
        return False
    return True


def run_coroutine(factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``factory()`` to completion from synchronous code.

    Takes a factory rather than a coroutine so that nothing is created until a
    runner has been chosen; a coroutine built and then not awaited is a warning
    at best and a silently skipped call at worst.
    """
    if in_anyio_worker_thread():
        import anyio.from_thread

        return anyio.from_thread.run(factory)
    return asyncio.run(factory())
