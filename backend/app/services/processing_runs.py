from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Document, ModelProvider, ProcessingRun, RunStatus
from app.services.model_provider import ModelSelection

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.PREPROCESSING,
    RunStatus.EXTRACTING,
    RunStatus.VALIDATING,
}


async def find_configured_run(
    session: AsyncSession,
    document_id: UUID,
    selection: ModelSelection,
    settings: Settings,
    *,
    include_failed: bool = False,
) -> ProcessingRun | None:
    statuses = [RunStatus.SUCCEEDED, *ACTIVE_RUN_STATUSES]
    if include_failed:
        statuses.extend([RunStatus.FAILED, RunStatus.CANCELLED])
    return await session.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.document_id == document_id,
            ProcessingRun.provider == ModelProvider(selection.provider),
            ProcessingRun.model_id == selection.model_id,
            ProcessingRun.prompt_version == settings.prompt_version,
            ProcessingRun.schema_version == settings.schema_version,
            ProcessingRun.preprocessing_version == settings.preprocessing_version,
            ProcessingRun.status.in_(statuses),
        )
        .order_by(
            (ProcessingRun.status == RunStatus.SUCCEEDED).desc(),
            ProcessingRun.queued_at.desc(),
            ProcessingRun.id.desc(),
        )
        .limit(1)
    )


def queue_processing_run(
    session: AsyncSession,
    document: Document,
    requested_by_user_id: UUID,
    selection: ModelSelection,
    settings: Settings,
    *,
    force: bool = False,
) -> ProcessingRun:
    prompt_version = settings.prompt_version
    if force:
        prompt_version = f"{prompt_version}:forced:{uuid4().hex}"
    run = ProcessingRun(
        document_id=document.id,
        requested_by_user_id=requested_by_user_id,
        provider=ModelProvider(selection.provider),
        model_id=selection.model_id,
        prompt_version=prompt_version,
        schema_version=settings.schema_version,
        preprocessing_version=settings.preprocessing_version,
        status=RunStatus.QUEUED,
        attempt_count=0,
    )
    session.add(run)
    return run
