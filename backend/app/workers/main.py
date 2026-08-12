"""Single-concurrency polling entry point for the PostgreSQL-backed worker."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from app.core.config import Settings, settings
from app.core.database import async_session_factory, engine
from app.services.object_storage import R2ObjectStorage
from app.services.upload_cleanup import cleanup_one_expired_upload
from app.workers.processor import process_next_document

logger = logging.getLogger("cassist.worker")


async def run_worker(
    *,
    app_settings: Settings = settings,
    stop_event: asyncio.Event | None = None,
    process_once: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Process at most one document at a time until shutdown is requested."""
    stopping = stop_event or asyncio.Event()
    storage: R2ObjectStorage | None = None

    async def configured_process() -> bool:
        nonlocal storage
        storage = storage or R2ObjectStorage(app_settings)
        async with async_session_factory() as session:
            await cleanup_one_expired_upload(
                session,
                storage,
            )
        return await process_next_document(app_settings=app_settings)

    process = process_once or configured_process
    while not stopping.is_set():
        try:
            processed = await process()
        except Exception:
            logger.error("Worker iteration failed; retrying after the polling interval")
            processed = False
        if processed or stopping.is_set():
            continue
        try:
            await asyncio.wait_for(stopping.wait(), timeout=app_settings.worker_poll_seconds)
        except TimeoutError:
            pass


async def _main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await run_worker(stop_event=stop_event)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
