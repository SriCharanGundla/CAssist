from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_app_settings, get_database_session
from app.core.config import Settings
from app.main import app
from app.models import (
    AuditEvent,
    Correction,
    Document,
    DocumentStatus,
    ExportEvent,
    ExportFormat,
    ExtractionResult,
    ModelProvider,
    ProcessingRun,
    RunStatus,
    WorkspaceMember,
)
from app.schemas.extraction import (
    ExtractedField,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTableRow,
    ExtractedTextBlock,
    GenericDocumentExtraction,
    QualityIssue,
)
from app.services.auth import establish_session
from app.services.identity_provider import VerifiedIdentity


@pytest_asyncio.fixture
async def result_client() -> AsyncIterator[
    tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID]
]:
    settings = Settings(
        app_env="test",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        connection = await engine.connect()
    except OSError:
        await engine.dispose()
        pytest.skip("Local PostgreSQL is unavailable")
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    identity = uuid4().hex
    user, credentials = await establish_session(
        session,
        VerifiedIdentity(
            issuer="https://identity.example/",
            subject=identity,
            email=f"review-{identity}@example.com",
            display_name="Review Tester",
            return_to="/",
        ),
        settings,
    )
    workspace_id = await session.scalar(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    assert workspace_id is not None
    document = Document(
        workspace_id=workspace_id,
        uploaded_by_user_id=user.id,
        original_filename="document.png",
        mime_type="image/png",
        byte_size=100,
        page_count=1,
        sha256="a" * 64,
        r2_object_key=f"originals/{uuid4().hex}",
        status=DocumentStatus.READY,
        upload_expires_at=None,
        original_deleted_at=None,
        original_deleted_by=None,
    )
    session.add(document)
    await session.flush()
    run = ProcessingRun(
        document_id=document.id,
        requested_by_user_id=user.id,
        provider=ModelProvider.GEMINI,
        model_id="test-model",
        prompt_version="test-prompt",
        schema_version="test-schema",
        preprocessing_version="test-preprocessing",
        status=RunStatus.SUCCEEDED,
        attempt_count=1,
    )
    session.add(run)
    await session.flush()
    extraction = GenericDocumentExtraction(
        document_type="invoice",
        fields=[
            ExtractedField(
                id="field-0001",
                label="Bill No.",
                value="INV-1",
                page_number=1,
            ),
            ExtractedField(
                id="field-0002",
                label="Customer Ref",
                value="1NV-1O2",
                page_number=1,
            ),
        ],
        tables=[
            ExtractedTable(
                id="table-0001",
                title="Particulars",
                headers=["Description", "Amount"],
                rows=[
                    ExtractedTableRow(
                        id="table-0001-row-0001",
                        cells=[
                            ExtractedTableCell(
                                id="table-0001-r0001-c0001",
                                value="Consulting",
                            ),
                            ExtractedTableCell(
                                id="table-0001-r0001-c0002",
                                value="100.00",
                            ),
                        ],
                    )
                ],
                page_numbers=[1],
            )
        ],
        text_blocks=[
            ExtractedTextBlock(
                id="text-0001",
                text="Thank you",
                page_number=1,
            )
        ],
    )
    quality_issue = QualityIssue(
        target_id="field-0002",
        code="possible_ocr_error",
        message="Possible character confusion",
        suggested_value="INV-102",
    )
    result = ExtractionResult(
        processing_run_id=run.id,
        document_type="invoice",
        raw_provider_output={"private": "provider output"},
        canonical_data=extraction.model_dump(mode="json"),
        validation_issues=[quality_issue.model_dump(mode="json")],
    )
    session.add(result)
    await session.commit()
    await session.refresh(result)

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_app_settings] = lambda: settings
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(settings.auth_session_cookie_name, credentials.session_token)
            client.headers["Origin"] = "http://localhost:5173"
            csrf_response = await client.get("/api/v1/auth/csrf")
            assert csrf_response.status_code == 200
            client.headers["X-CSRF-Token"] = csrf_response.json()["csrf_token"]
            yield client, session, settings, user.id, run.id, result.id
    finally:
        app.dependency_overrides.clear()
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_result_returns_generic_data_without_provider_output(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, _, _, _, run_id, _ = result_client

    response = await client.get(f"/api/v1/runs/{run_id}/result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["review_status"] == "unreviewed"
    assert payload["extracted_data"] == payload["effective_data"]
    assert payload["effective_data"]["fields"][0] == {
        "id": "field-0001",
        "label": "Bill No.",
        "value": "INV-1",
        "page_number": 1,
        "region": None,
    }
    assert [issue["code"] for issue in payload["quality_issues"]] == [
        "possible_ocr_error"
    ]
    assert "raw_provider_output" not in payload
    assert "provider output" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_get_result_by_id_supports_reloadable_review_route(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, _, _, _, run_id, result_id = result_client
    response = await client.get(f"/api/v1/results/{result_id}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["result_id"] == str(result_id)
    assert response.json()["run_id"] == str(run_id)
    assert "raw_provider_output" not in response.json()


@pytest.mark.asyncio
async def test_corrections_are_append_only_and_remove_resolved_quality_issue(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, user_id, _, result_id = result_client
    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [
                {
                    "target_id": "field-0002",
                    "value": "INV-102",
                    "reason": "Accepted after checking the image",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["review_status"] == "in_review"
    assert payload["extracted_data"]["fields"][1]["value"] == "1NV-1O2"
    assert payload["effective_data"]["fields"][1]["value"] == "INV-102"
    assert payload["quality_issues"] == []
    assert payload["corrections"][0]["target_id"] == "field-0002"
    assert payload["corrections"][0]["previous_value"] == "1NV-1O2"
    assert payload["corrections"][0]["corrected_by_user_id"] == str(user_id)

    stored_result = await session.get(ExtractionResult, result_id)
    assert stored_result is not None
    assert stored_result.canonical_data["fields"][1]["value"] == "1NV-1O2"
    assert stored_result.validation_issues == []
    correction_count = await session.scalar(
        select(func.count())
        .select_from(Correction)
        .where(Correction.extraction_result_id == result_id)
    )
    audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == result_id,
            AuditEvent.action == "result.corrected",
        )
    )
    assert correction_count == 1
    assert audit is not None
    assert audit.metadata_ == {"correction_count": 1, "result_version": 2}

    stale = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [{"target_id": "field-0001", "value": "INV-2"}],
        },
    )
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_invalid_target_is_rejected_without_partial_writes(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, _, _, result_id = result_client
    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [
                {"target_id": "field-0001", "value": "INV-2"},
                {"target_id": "missing", "value": "invalid"},
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Correction target does not exist"
    result = await session.get(ExtractionResult, result_id)
    assert result is not None and result.version == 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Correction)
            .where(Correction.extraction_result_id == result_id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_multiple_corrections_rebuild_in_request_order(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, _, _, _, run_id, result_id = result_client
    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [
                {"target_id": "field-0001", "value": "INV-2"},
                {"target_id": "field-0001", "value": "INV-3"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["effective_data"]["fields"][0]["value"] == "INV-3"
    assert [item["previous_value"] for item in response.json()["corrections"]] == [
        "INV-1",
        "INV-2",
    ]

    rebuilt = await client.get(f"/api/v1/runs/{run_id}/result")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["effective_data"]["fields"][0]["value"] == "INV-3"


@pytest.mark.asyncio
async def test_approval_is_audited_and_later_correction_reopens_review(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, user_id, _, result_id = result_client
    approved = await client.post(
        f"/api/v1/results/{result_id}/review",
        json={"expected_version": 1, "status": "approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["reviewed_by_user_id"] == str(user_id)

    corrected = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 2,
            "changes": [{"target_id": "field-0002", "value": "INV-102"}],
        },
    )
    assert corrected.status_code == 200
    assert corrected.json()["version"] == 3
    assert corrected.json()["review_status"] == "in_review"
    assert corrected.json()["reviewed_by_user_id"] is None
    assert corrected.json()["reviewed_at"] is None

    audits = list(
        (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.entity_id == result_id).order_by(AuditEvent.id)
            )
        ).all()
    )
    assert [audit.action for audit in audits] == [
        "result.review_status_changed",
        "result.corrected",
    ]


@pytest.mark.asyncio
async def test_result_operations_hide_other_workspaces(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, settings, _, run_id, result_id = result_client
    identity = uuid4().hex
    _, credentials = await establish_session(
        session,
        VerifiedIdentity(
            issuer="https://identity.example/",
            subject=identity,
            email=f"outsider-{identity}@example.com",
            display_name="Other Workspace",
            return_to="/",
        ),
        settings,
    )
    client.cookies.set(settings.auth_session_cookie_name, credentials.session_token)
    csrf_response = await client.get("/api/v1/auth/csrf")
    assert csrf_response.status_code == 200
    client.headers["X-CSRF-Token"] = csrf_response.json()["csrf_token"]

    assert (await client.get(f"/api/v1/runs/{run_id}/result")).status_code == 404
    assert (await client.get(f"/api/v1/results/{result_id}")).status_code == 404
    correction = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [{"target_id": "field-0001", "value": "Hidden"}],
        },
    )
    assert correction.status_code == 404
    review = await client.post(
        f"/api/v1/results/{result_id}/review",
        json={"expected_version": 1, "status": "approved"},
    )
    assert review.status_code == 404
    export = await client.post(
        f"/api/v1/results/{result_id}/exports",
        json={"expected_version": 1, "format": "tally_json"},
    )
    assert export.status_code == 404


@pytest.mark.asyncio
async def test_tally_handoff_requires_approved_current_result(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, _, _, result_id = result_client
    unapproved = await client.post(
        f"/api/v1/results/{result_id}/exports",
        json={"expected_version": 1, "format": "tally_json"},
    )
    assert unapproved.status_code == 409

    approved = await client.post(
        f"/api/v1/results/{result_id}/review",
        json={"expected_version": 1, "status": "approved"},
    )
    assert approved.status_code == 200
    stale = await client.post(
        f"/api/v1/results/{result_id}/exports",
        json={"expected_version": 1, "format": "tally_json"},
    )
    assert stale.status_code == 409
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ExportEvent)
            .where(ExportEvent.extraction_result_id == result_id)
        )
        == 0
    )

    current = await client.post(
        f"/api/v1/results/{result_id}/exports",
        json={"expected_version": 2, "format": "tally_json"},
    )
    assert current.status_code == 200
    assert current.json()["quality_issues"][0]["code"] == "possible_ocr_error"


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    return False


@pytest.mark.asyncio
async def test_tally_handoff_preserves_effective_strings_and_records_safe_events(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, user_id, _, result_id = result_client
    corrected = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [{"target_id": "field-0002", "value": "INV-102"}],
        },
    )
    assert corrected.status_code == 200
    approved = await client.post(
        f"/api/v1/results/{result_id}/review",
        json={"expected_version": 2, "status": "approved"},
    )
    assert approved.status_code == 200

    response = await client.post(
        f"/api/v1/results/{result_id}/exports",
        json={
            "expected_version": 3,
            "format": "tally_json",
            "options": {"include_quality_issues": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "tally-handoff-v2"
    assert payload["tally_compatibility"]["native_import_ready"] is False
    assert payload["reviewed_extraction"]["fields"][1]["value"] == "INV-102"
    assert payload["reviewed_extraction"]["tables"][0]["rows"][0]["cells"][1][
        "value"
    ] == "100.00"
    assert "quality_issues" not in payload
    assert not _contains_float(payload)
    assert {mapping["code"] for mapping in payload["required_mappings"]} == {
        "TARGET_COMPANY",
        "VOUCHER_TYPE",
        "DOCUMENT_FIELDS",
        "LEDGER_AND_ITEM_MASTERS",
    }

    export_event = await session.scalar(
        select(ExportEvent).where(ExportEvent.extraction_result_id == result_id)
    )
    audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == result_id,
            AuditEvent.action == "result.exported",
        )
    )
    assert export_event is not None
    assert export_event.exported_by_user_id == user_id
    assert export_event.format == ExportFormat.TALLY_JSON
    assert export_event.exporter_version == "tally-handoff-v2"
    assert export_event.options == {
        "include_quality_issues": False,
        "result_version": 3,
        "native_import_ready": False,
    }
    assert audit is not None
    assert audit.metadata_ == {
        "format": "tally_json",
        "exporter_version": "tally-handoff-v2",
        "result_version": 3,
    }
