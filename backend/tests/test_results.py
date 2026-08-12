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
from app.schemas.extraction import CanonicalInvoice, InvoiceTotals, Party
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
        original_filename="invoice.png",
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
    invoice = CanonicalInvoice(
        invoice_number="INV-1",
        invoice_date="2026-08-12",
        supplier=Party(name="Supplier"),
        buyer=Party(),
        totals=InvoiceTotals(grand_total="100.00"),
    )
    result = ExtractionResult(
        processing_run_id=run.id,
        document_type="tax_invoice",
        raw_provider_output={"private": "provider output"},
        canonical_data=invoice.model_dump(mode="json"),
        validation_issues=[
            {
                "severity": "warning",
                "code": "MISSING_PARTY_NAME",
                "field_path": "/buyer/name",
                "message": "Buyer name was not extracted",
            }
        ],
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
async def test_get_result_returns_effective_data_without_provider_output(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, _, _, _, run_id, _ = result_client

    response = await client.get(f"/api/v1/runs/{run_id}/result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["review_status"] == "unreviewed"
    assert payload["canonical_data"] == payload["effective_data"]
    assert [issue["code"] for issue in payload["validation_issues"]] == [
        "MISSING_PARTY_NAME",
        "NO_LINE_ITEMS",
    ]
    assert "raw_provider_output" not in payload
    assert "provider output" not in response.text


@pytest.mark.asyncio
async def test_corrections_are_append_only_revalidated_audited_and_versioned(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, user_id, _, result_id = result_client

    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [
                {
                    "field_path": "/buyer/name",
                    "value": "Buyer Ltd",
                    "reason": "Corrected from the document image",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["review_status"] == "in_review"
    assert payload["canonical_data"]["buyer"]["name"] is None
    assert payload["effective_data"]["buyer"]["name"] == "Buyer Ltd"
    assert "MISSING_PARTY_NAME" not in {issue["code"] for issue in payload["validation_issues"]}
    assert payload["corrections"][0]["previous_value"] is None
    assert payload["corrections"][0]["corrected_by_user_id"] == str(user_id)

    stored_result = await session.get(ExtractionResult, result_id)
    assert stored_result is not None
    assert stored_result.canonical_data["buyer"]["name"] is None
    assert "MISSING_PARTY_NAME" not in {issue["code"] for issue in stored_result.validation_issues}
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
            "changes": [{"field_path": "/invoice_number", "value": "INV-2"}],
        },
    )
    assert stale.status_code == 409
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Correction)
            .where(Correction.extraction_result_id == result_id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_invalid_correction_is_rejected_without_partial_writes(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, _, _, result_id = result_client

    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [
                {"field_path": "/invoice_number", "value": "INV-2"},
                {"field_path": "/totals/grand_total", "value": 100.0},
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Corrected data does not match the canonical invoice schema"
    )
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
                {"field_path": "/invoice_number", "value": "INV-2"},
                {"field_path": "/invoice_number", "value": "INV-3"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["effective_data"]["invoice_number"] == "INV-3"
    assert [item["previous_value"] for item in response.json()["corrections"]] == [
        "INV-1",
        "INV-2",
    ]

    rebuilt = await client.get(f"/api/v1/runs/{run_id}/result")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["effective_data"]["invoice_number"] == "INV-3"


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
    assert approved.json()["version"] == 2
    assert approved.json()["review_status"] == "approved"
    assert approved.json()["reviewed_by_user_id"] == str(user_id)
    assert approved.json()["reviewed_at"] is not None

    corrected = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 2,
            "changes": [{"field_path": "/buyer/name", "value": "Buyer Ltd"}],
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
    assert all(
        set(audit.metadata_) <= {"review_status", "result_version", "correction_count"}
        for audit in audits
    )


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
    assert (
        await client.patch(
            f"/api/v1/results/{result_id}/fields",
            json={
                "expected_version": 1,
                "changes": [{"field_path": "/buyer/name", "value": "Hidden"}],
            },
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/results/{result_id}/review",
            json={"expected_version": 1, "status": "approved"},
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/results/{result_id}/exports",
            json={"expected_version": 1, "format": "tally_json"},
        )
    ).status_code == 404


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
    assert unapproved.json()["detail"] == "Result must be approved before export"

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
    assert stale.json()["detail"] == "Result changed; reload before exporting"
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
    assert {issue["code"] for issue in current.json()["validation_warnings"]} == {
        "MISSING_PARTY_NAME",
        "NO_LINE_ITEMS",
    }


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    return False


@pytest.mark.asyncio
async def test_tally_handoff_exports_effective_strings_and_records_safe_events(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
) -> None:
    client, session, _, user_id, _, result_id = result_client
    corrected = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [{"field_path": "/buyer/name", "value": "Buyer Ltd"}],
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
            "options": {"include_validation_warnings": False},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="cassist-tally-{result_id}.json"'
    )
    payload = response.json()
    assert payload["format"] == "cassist.tally_handoff"
    assert payload["schema_version"] == "tally-handoff-v1"
    assert payload["tally_compatibility"] == {
        "target": "TallyPrime 7.0+",
        "native_import_ready": False,
        "reason_code": "COMPANY_AND_MASTER_MAPPING_REQUIRED",
    }
    assert payload["source"]["review_status"] == "approved"
    assert payload["source"]["result_version"] == 3
    assert payload["voucher_draft"]["date"] == "20260812"
    assert payload["voucher_draft"]["buyer"]["name"] == "Buyer Ltd"
    assert payload["voucher_draft"]["totals"]["grand_total"] == "100.00"
    assert "validation_warnings" not in payload
    assert not _contains_float(payload)
    assert {mapping["code"] for mapping in payload["required_mappings"]} == {
        "TARGET_COMPANY",
        "TRANSACTION_ROLE",
        "PARTY_LEDGER",
        "ACCOUNTING_MODE",
        "LINE_MASTERS",
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
    assert export_event.exporter_version == "tally-handoff-v1"
    assert export_event.options == {
        "include_validation_warnings": False,
        "result_version": 3,
        "native_import_ready": False,
    }
    assert audit is not None
    assert audit.metadata_ == {
        "format": "tally_json",
        "exporter_version": "tally-handoff-v1",
        "result_version": 3,
    }


@pytest.mark.parametrize(
    ("field_path", "expected_detail"),
    [
        ("/missing", "JSON Pointer field does not exist"),
        ("/buyer~2name", "JSON Pointer contains an invalid escape"),
        ("/line_items/-", "JSON Pointer list index is invalid"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_json_pointer_paths_are_rejected(
    result_client: tuple[AsyncClient, AsyncSession, Settings, UUID, UUID, UUID],
    field_path: str,
    expected_detail: str,
) -> None:
    client, _, _, _, _, result_id = result_client
    response = await client.patch(
        f"/api/v1/results/{result_id}/fields",
        json={
            "expected_version": 1,
            "changes": [{"field_path": field_path, "value": "invalid"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == expected_detail
