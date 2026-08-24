import asyncio

import pytest

from app.core.config import Settings
from app.workers import main as worker_main
from app.workers.main import run_worker


@pytest.mark.asyncio
async def test_worker_processes_sequentially_until_stopped() -> None:
    stop_event = asyncio.Event()
    active = 0
    maximum_active = 0
    calls = 0

    async def process_once() -> bool:
        nonlocal active, calls, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        calls += 1
        await asyncio.sleep(0)
        active -= 1
        if calls == 3:
            stop_event.set()
        return True

    await run_worker(
        app_settings=Settings(app_env="test", _env_file=None),
        stop_event=stop_event,
        process_once=process_once,
    )

    assert calls == 3
    assert maximum_active == 1


@pytest.mark.asyncio
async def test_worker_recovers_from_safe_iteration_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("ERROR", logger="cassist.worker")
    stop_event = asyncio.Event()
    calls = 0

    async def process_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private upstream detail")
        stop_event.set()
        return False

    await run_worker(
        app_settings=Settings(
            app_env="test",
            worker_poll_seconds=0.001,
            _env_file=None,
        ),
        stop_event=stop_event,
        process_once=process_once,
    )

    assert calls == 2
    assert "private upstream detail" not in caplog.text
    assert "Worker iteration failed" in caplog.text


@pytest.mark.asyncio
async def test_worker_uses_bounded_exponential_backoff_for_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = asyncio.Event()
    calls = 0
    timeouts: list[float] = []

    async def process_once() -> bool:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise RuntimeError("infrastructure unavailable")
        stop_event.set()
        return False

    async def record_wait(awaitable, *, timeout: float):
        awaitable.close()
        timeouts.append(timeout)
        raise TimeoutError

    monkeypatch.setattr(worker_main.asyncio, "wait_for", record_wait)

    await run_worker(
        app_settings=Settings(
            app_env="test",
            worker_poll_seconds=2,
            _env_file=None,
        ),
        stop_event=stop_event,
        process_once=process_once,
    )

    assert timeouts == [2, 4, 8]


def test_session_cleanup_batch_size_is_configurable_and_bounded() -> None:
    settings = Settings(
        app_env="test",
        session_cleanup_batch_size=250,
        _env_file=None,
    )
    assert settings.session_cleanup_batch_size == 250

    with pytest.raises(ValueError, match="SESSION_CLEANUP_BATCH_SIZE"):
        Settings(
            app_env="test",
            session_cleanup_batch_size=0,
            _env_file=None,
        )
