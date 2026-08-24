import json
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_app_settings, get_database_session, require_csrf
from app.core.config import Settings
from app.models import (
    AuditEvent,
    Correction,
    DocumentStatus,
    ExtractionResult,
    ModelProvider,
    ProcessingRun,
    RunStatus,
)
from app.schemas.documents import (
    ComparisonAgreementResponse,
    ComparisonDifferenceResponse,
    ComparisonResponse,
    ComparisonRunResponse,
)
from app.services.auth import CurrentAuth
from app.services.document_access import (
    authorized_document,
    require_available_original,
    require_scope_allows_new_run,
)
from app.services.model_provider import ModelSelection
from app.services.processing_runs import find_configured_run, queue_processing_run

router = APIRouter(prefix="/documents")


def _observations(result: ExtractionResult) -> Counter[str]:
    data = result.canonical_data
    observations: list[str] = []
    for field in data.get("fields", []):
        if isinstance(field, dict):
            observations.append(json.dumps(["field", field.get("label"), field.get("value")]))
    for table in data.get("tables", []):
        if not isinstance(table, dict):
            continue
        table_title = table.get("title") or "Table"
        headers = table.get("headers", [])
        observations.extend(json.dumps(["table_header", table_title, value]) for value in headers)
        for row in table.get("rows", []):
            if isinstance(row, dict):
                for index, cell in enumerate(row.get("cells", [])):
                    if not isinstance(cell, dict):
                        continue
                    header = headers[index] if index < len(headers) else f"Column {index + 1}"
                    observations.append(
                        json.dumps(["table_cell", f"{table_title} · {header}", cell.get("value")])
                    )
    for block in data.get("text_blocks", []):
        if isinstance(block, dict):
            observations.append(json.dumps(["text", None, block.get("text")]))
    return Counter(observations)


def _comparison_differences(
    gemini: Counter[str],
    openai: Counter[str],
) -> list[ComparisonDifferenceResponse]:
    differences: list[ComparisonDifferenceResponse] = []
    for observation in sorted(gemini.keys() | openai.keys()):
        gemini_count = gemini[observation]
        openai_count = openai[observation]
        if gemini_count == openai_count:
            continue
        kind, label, value = json.loads(observation)
        differences.append(
            ComparisonDifferenceResponse(
                kind=kind,
                label=label,
                value=value,
                gemini_count=gemini_count,
                openai_count=openai_count,
            )
        )
    return differences


@router.post("/{document_id}/comparisons", response_model=ComparisonResponse)
async def compare_document_models(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ComparisonResponse:
    if app_settings.app_env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    require_available_original(document)
    require_scope_allows_new_run(document)

    selections = (
        ModelSelection("gemini", app_settings.comparison_gemini_model_id),
        ModelSelection("openai", app_settings.comparison_openai_model_id),
    )
    runs: list[tuple[ProcessingRun, bool]] = []
    queued = False
    for selection in selections:
        run = await find_configured_run(
            session,
            document.id,
            selection,
            app_settings,
            include_failed=True,
        )
        cache_hit = run is not None and run.status == RunStatus.SUCCEEDED
        if run is None:
            run = queue_processing_run(
                session,
                document,
                current_auth.user.id,
                selection,
                app_settings,
            )
            queued = True
        runs.append((run, cache_hit))
    if queued:
        document.status = DocumentStatus.UPLOADED
        document.updated_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                workspace_id=document.workspace_id,
                actor_user_id=current_auth.user.id,
                action="document.comparison_requested",
                entity_type="document",
                entity_id=document.id,
                metadata_={"provider_count": len(selections)},
            )
        )
        await session.commit()

    run_responses: list[ComparisonRunResponse] = []
    successful_results: dict[ModelProvider, ExtractionResult] = {}
    for run, cache_hit in runs:
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run.id)
        )
        if result is not None:
            successful_results[run.provider] = result
        correction_count = None
        if result is not None:
            correction_count = await session.scalar(
                select(func.count()).where(Correction.extraction_result_id == result.id)
            )
        latency_ms = None
        if run.started_at is not None and run.completed_at is not None:
            latency_ms = max(0, int((run.completed_at - run.started_at).total_seconds() * 1000))
        run_responses.append(
            ComparisonRunResponse(
                provider=run.provider,
                model_id=run.model_id,
                run_id=run.id,
                status=run.status,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                estimated_cost_usd=(
                    str(run.estimated_cost_usd) if run.estimated_cost_usd is not None else None
                ),
                quality_issue_count=(len(result.validation_issues) if result is not None else None),
                correction_count=correction_count,
                structural_failure=run.status in {RunStatus.FAILED, RunStatus.CANCELLED},
            )
        )

    agreement = None
    if set(successful_results) == {ModelProvider.GEMINI, ModelProvider.OPENAI}:
        left = _observations(successful_results[ModelProvider.GEMINI])
        right = _observations(successful_results[ModelProvider.OPENAI])
        compared = max(sum(left.values()), sum(right.values()))
        matching = sum((left & right).values())
        differences = _comparison_differences(left, right)
        agreement = ComparisonAgreementResponse(
            compared_observations=compared,
            matching_observations=matching,
            match_rate=round(matching / compared, 4) if compared else 1.0,
            difference_count=len(differences),
            differences=differences[:200],
        )
    return ComparisonResponse(document_id=document.id, runs=run_responses, agreement=agreement)
