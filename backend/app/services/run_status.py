from app.models import Document, ExtractionResult, ProcessingRun, RunStatus
from app.schemas.documents import (
    RunDetailResponse,
    RunErrorResponse,
    RunProgressResponse,
    RunSummaryResponse,
)

_ALL_PAGES_COMPLETE = {
    RunStatus.VALIDATING,
    RunStatus.SUCCEEDED,
}


def run_summary(
    run: ProcessingRun,
    result: ExtractionResult | None,
) -> RunSummaryResponse:
    return RunSummaryResponse(
        id=run.id,
        status=run.status,
        provider=run.provider,
        model_id=run.model_id,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        result_id=result.id if result is not None else None,
        review_status=result.review_status if result is not None else None,
    )


def run_detail(
    run: ProcessingRun,
    document: Document,
    result: ExtractionResult | None,
) -> RunDetailResponse:
    summary = run_summary(run, result)
    completed_pages = document.page_count if run.status in _ALL_PAGES_COMPLETE else None
    error = None
    if run.error_code and run.error_message_safe:
        error = RunErrorResponse(code=run.error_code, message=run.error_message_safe)
    return RunDetailResponse(
        **summary.model_dump(),
        document_id=document.id,
        attempt_count=run.attempt_count,
        progress=RunProgressResponse(
            stage=run.status,
            completed_pages=completed_pages,
            total_pages=document.page_count,
        ),
        error=error,
    )
