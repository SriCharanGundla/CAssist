from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.core.database import async_session_factory
from app.models import MemberRole, WorkspaceMember
from app.services.auth import (
    AuthenticationRequired,
    CsrfValidationError,
    CurrentAuth,
    resolve_session,
    verify_csrf,
)
from app.services.identity_provider import Auth0IdentityProvider, IdentityProvider


async def get_database_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


def get_app_settings() -> Settings:
    return settings


def get_identity_provider(
    app_settings: Annotated[Settings, Depends(get_app_settings)],
) -> IdentityProvider:
    if not app_settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )
    return Auth0IdentityProvider(app_settings)


async def get_current_auth(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentAuth:
    try:
        return await resolve_session(
            session,
            request.cookies.get(app_settings.auth_session_cookie_name),
            app_settings,
        )
    except AuthenticationRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
        ) from exc


async def require_csrf(
    request: Request,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentAuth:
    try:
        verify_csrf(request, current_auth, app_settings)
    except CsrfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        ) from exc
    return current_auth


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace_id: UUID
    user_id: UUID
    role: MemberRole


async def require_workspace_member(
    workspace_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> WorkspaceAccess:
    role = await session.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_auth.user.id,
        )
    )
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceAccess(
        workspace_id=workspace_id,
        user_id=current_auth.user.id,
        role=role,
    )


def require_workspace_roles(*allowed_roles: MemberRole):
    async def dependency(
        access: Annotated[WorkspaceAccess, Depends(require_workspace_member)],
    ) -> WorkspaceAccess:
        if access.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace role does not permit this action",
            )
        return access

    return dependency
