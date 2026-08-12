import asyncio

import pytest

from app.core.config import Settings
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
