"""Exercise the complete local MVP with generated synthetic accounting data."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import httpx
from live_model_smoke import _synthetic_invoice
from sqlalchemy import delete, func, select

from app.api.dependencies import get_current_auth
from app.core.config import Settings
from app.core.database import async_session_factory, engine
from app.main import app
from app.models import (
    AuthSession,
    Document,
    MemberRole,
    ProcessingRun,
    RunStatus,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services.auth import CurrentAuth, create_opaque_token, hash_token
from app.services.object_storage import ObjectStorageError, R2ObjectStorage
from app.workers.processor import process_next_document

ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.PREPROCESSING,
    RunStatus.EXTRACTING,
    RunStatus.VALIDATING,
)


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/auth/csrf")
    response.raise_for_status()
    return {"X-CSRF-Token": response.json()["csrf_token"]}


async def _cleanup(workspace_id: UUID | None, user_id: UUID | None, settings: Settings) -> None:
    if workspace_id is None or user_id is None:
        return
    storage = R2ObjectStorage(settings)
    async with async_session_factory() as session:
        object_keys = list(
            (
                await session.scalars(
                    select(Document.r2_object_key).where(
                        Document.workspace_id == workspace_id,
                        Document.r2_object_key.is_not(None),
                    )
                )
            ).all()
        )
        for object_key in object_keys:
            if object_key is not None:
                try:
                    await asyncio.to_thread(storage.delete_object, object_key)
                except ObjectStorageError:
                    pass
        await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def run() -> int:
    settings = Settings()
    if settings.app_env == "production":
        print("REFUSED: local vertical smoke test cannot run in production")
        return 2
    if not settings.r2_configured or not settings.gemini_api_key:
        print("BLOCKED: development R2 and Gemini credentials are required")
        return 2

    workspace_id: UUID | None = None
    user_id: UUID | None = None
    stage = "preflight"
    try:
        async with async_session_factory() as session:
            active_count = await session.scalar(
                select(func.count())
                .select_from(ProcessingRun)
                .where(ProcessingRun.status.in_(ACTIVE_RUN_STATUSES))
            )
            if active_count:
                print("BLOCKED: existing active processing runs must finish first")
                return 2

            marker = uuid4().hex
            current_time = datetime.now(UTC)
            user = User(
                external_auth_id=f"synthetic-smoke|{marker}",
                email=f"synthetic-smoke-{marker}@example.invalid",
                display_name="Synthetic Smoke Test",
                last_seen_at=current_time,
            )
            session.add(user)
            await session.flush()
            workspace = Workspace(name="Synthetic Smoke Test", created_by_user_id=user.id)
            session.add(workspace)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=MemberRole.OWNER,
                )
            )
            auth_session = AuthSession(
                user_id=user.id,
                token_hash=hash_token(create_opaque_token()),
                csrf_token_hash=hash_token(create_opaque_token()),
                last_seen_at=current_time,
                idle_expires_at=current_time + timedelta(hours=1),
                absolute_expires_at=current_time + timedelta(hours=1),
            )
            session.add(auth_session)
            await session.commit()
            user_id = user.id
            workspace_id = workspace.id
            auth_session_id = auth_session.id

        async def current_smoke_auth() -> CurrentAuth:
            async with async_session_factory() as session:
                current_session = await session.get(AuthSession, auth_session_id)
                current_user = await session.get(User, user_id)
                if current_session is None or current_user is None:
                    raise RuntimeError("Synthetic smoke identity disappeared")
                return CurrentAuth(
                    session_id=current_session.id,
                    user=current_user,
                    csrf_token_hash=current_session.csrf_token_hash,
                )

        app.dependency_overrides[get_current_auth] = current_smoke_auth

        with TemporaryDirectory(prefix="cassist-vertical-smoke-") as directory:
            stage = "fixture_generation"
            invoice_path = Path(directory) / "synthetic-invoice.png"
            _synthetic_invoice(invoice_path)
            invoice_bytes = invoice_path.read_bytes()

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Origin": "http://localhost:5173"},
            ) as client:
                stage = "upload_creation"
                create = await client.post(
                    "/api/v1/uploads",
                    headers=await _csrf(client),
                    json={
                        "filename": "synthetic-invoice.png",
                        "mime_type": "image/png",
                        "byte_size": len(invoice_bytes),
                    },
                )
                create.raise_for_status()
                created = create.json()
                document_id = created["document_id"]

                async with httpx.AsyncClient() as external_client:
                    stage = "private_r2_upload"
                    put = await external_client.put(
                        created["upload"]["url"],
                        headers=created["upload"]["headers"],
                        content=invoice_bytes,
                    )
                    put.raise_for_status()

                stage = "trusted_upload_completion"
                complete = await client.post(
                    f"/api/v1/uploads/{document_id}/complete",
                    headers=await _csrf(client),
                )
                complete.raise_for_status()
                assert complete.json()["status"] == "uploaded"

                stage = "worker_extraction"
                assert await process_next_document(app_settings=settings) is True
                stage = "document_status"
                document = await client.get(f"/api/v1/documents/{document_id}")
                document.raise_for_status()
                document_payload = document.json()
                if document_payload["status"] != "ready":
                    run_id = document_payload["latest_run"]["id"]
                    failed_run = await client.get(f"/api/v1/runs/{run_id}")
                    failed_run.raise_for_status()
                    error_code = (failed_run.json().get("error") or {}).get("code")
                    stage = f"document_status_{document_payload['status']}_{error_code or 'none'}"
                    raise AssertionError
                result_id = document_payload["latest_run"]["result_id"]
                assert result_id is not None

                stage = "result_retrieval"
                result = await client.get(f"/api/v1/results/{result_id}")
                result.raise_for_status()
                result_payload = result.json()
                fields = result_payload["effective_data"]["fields"]
                buyer = next(
                    field
                    for field in fields
                    if field["value"] == "Example Buyer LLP"
                )

                stage = "correction"
                correction = await client.patch(
                    f"/api/v1/results/{result_id}/fields",
                    headers=await _csrf(client),
                    json={
                        "expected_version": result_payload["version"],
                        "changes": [
                            {
                                "target_id": buyer["id"],
                                "value": "Example Buyer LLP (Reviewed)",
                                "reason": "Synthetic vertical smoke correction",
                            }
                        ],
                    },
                )
                correction.raise_for_status()
                corrected = correction.json()
                assert corrected["review_status"] == "in_review"

                stage = "approval"
                approval = await client.post(
                    f"/api/v1/results/{result_id}/review",
                    headers=await _csrf(client),
                    json={
                        "expected_version": corrected["version"],
                        "status": "approved",
                    },
                )
                approval.raise_for_status()
                approved = approval.json()
                assert approved["review_status"] == "approved"

                stage = "tally_export"
                export = await client.post(
                    f"/api/v1/results/{result_id}/exports",
                    headers=await _csrf(client),
                    json={
                        "expected_version": approved["version"],
                        "format": "tally_json",
                        "options": {"include_quality_issues": True},
                    },
                )
                export.raise_for_status()
                assert export.headers["content-type"].startswith("application/json")
                assert export.json()["tally_compatibility"]["native_import_ready"] is False

                stage = "original_retrieval"
                view = await client.post(
                    f"/api/v1/documents/{document_id}/view-url",
                    headers=await _csrf(client),
                )
                view.raise_for_status()
                async with httpx.AsyncClient() as external_client:
                    original = await external_client.get(view.json()["url"])
                    original.raise_for_status()
                    assert original.content == invoice_bytes

                stage = "permanent_deletion"
                removal = await client.delete(
                    f"/api/v1/documents/{document_id}",
                    headers=await _csrf(client),
                )
                removal.raise_for_status()

        print("PASS: authenticated synthetic upload-to-delete vertical smoke test")
        return 0
    except httpx.HTTPStatusError as exc:
        try:
            error_code = exc.response.json().get("error", {}).get("code", "unknown")
        except (TypeError, ValueError):
            error_code = "unknown"
        print(
            f"FAILED: vertical smoke stage={stage}; "
            f"http_status={exc.response.status_code}; error_code={error_code}"
        )
        return 1
    except Exception as exc:
        print(f"FAILED: vertical smoke stage={stage}; error_type={type(exc).__name__}")
        return 1
    finally:
        app.dependency_overrides.pop(get_current_auth, None)
        await _cleanup(workspace_id, user_id, settings)
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
