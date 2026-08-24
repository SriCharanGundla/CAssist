"""Single-concurrency polling entry point for the PostgreSQL-backed worker."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from app.core.config import Settings, settings
from app.core.database import async_session_factory, engine
from app.core.safe_logging import safe_exception_context
from app.services.object_deletion import process_one_object_deletion
from app.services.object_storage import R2ObjectStorage
from app.services.session_cleanup import cleanup_expired_sessions
from app.services.upload_cleanup import cleanup_one_expired_upload
from app.workers.processor import process_next_document

logger = logging.getLogger("cassist.worker")
_MAX_ERROR_BACKOFF_SECONDS = 60.0


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
            await cleanup_expired_sessions(
                session,
                limit=app_settings.session_cleanup_batch_size,
            )
            await cleanup_one_expired_upload(
                session,
                storage,
            )
            await process_one_object_deletion(session, storage)
        return await process_next_document(app_settings=app_settings)

    process = process_once or configured_process
    error_backoff_seconds = app_settings.worker_poll_seconds
    while not stopping.is_set():
        iteration_failed = False
        try:
            processed = await process()
            error_backoff_seconds = app_settings.worker_poll_seconds
        except Exception as exc:
            logger.error(
                "Worker iteration failed; retrying with bounded backoff",
                extra={
                    "backoff_seconds": error_backoff_seconds,
                    **safe_exception_context(exc),
                },
            )
            processed = False
            iteration_failed = True
        if processed or stopping.is_set():
            continue
        wait_seconds = (
            error_backoff_seconds if iteration_failed else app_settings.worker_poll_seconds
        )
        if iteration_failed:
            error_backoff_seconds = min(
                _MAX_ERROR_BACKOFF_SECONDS,
                error_backoff_seconds * 2,
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=wait_seconds)
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
