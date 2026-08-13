from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_app_settings,
    get_current_auth,
    get_database_session,
    get_identity_provider,
    require_csrf,
)
from app.core.config import Settings
from app.models import AuthSession, Workspace, WorkspaceMember
from app.schemas.auth import (
    AuthSessionPageResponse,
    AuthSessionResponse,
    AuthUserResponse,
    CsrfTokenResponse,
    CurrentAuthResponse,
    LogoutResponse,
    WorkspaceMembershipResponse,
)
from app.services.auth import (
    AccessRestricted,
    AccountLinkRequired,
    CsrfValidationError,
    CurrentAuth,
    SessionCredentials,
    establish_session,
    revoke_session,
    rotate_csrf_token,
    session_device_label,
    validate_return_to,
    verify_request_origin,
)
from app.services.identity_provider import IdentityProvider, IdentityProviderError

router = APIRouter(prefix="/auth")


def set_auth_cookies(
    response: RedirectResponse,
    credentials: SessionCredentials,
    settings: Settings,
) -> None:
    max_age = max(0, int((credentials.absolute_expires_at - datetime.now(UTC)).total_seconds()))
    cookie_options = {
        "max_age": max_age,
        "expires": credentials.absolute_expires_at,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(
        settings.auth_session_cookie_name,
        credentials.session_token,
        httponly=True,
        **cookie_options,
    )


def clear_auth_cookies(response: JSONResponse, settings: Settings) -> None:
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/login")
async def login(
    request: Request,
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    return_to: Annotated[str | None, Query()] = None,
):
    try:
        safe_return_to = validate_return_to(return_to)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await provider.start_login(
        request,
        app_settings.auth_callback_url,
        safe_return_to,
    )


@router.get("/callback")
async def callback(
    request: Request,
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
):
    try:
        identity = await provider.complete_login(request)
        return_to = validate_return_to(identity.return_to)
        _, credentials = await establish_session(
            session,
            identity,
            app_settings,
            user_agent=request.headers.get("user-agent"),
        )
    except IdentityProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        ) from exc
    except AccessRestricted as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to CAssist is restricted",
        ) from exc
    except AccountLinkRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account linking requires administrator review",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    response = RedirectResponse(
        url=f"{app_settings.frontend_url.rstrip('/')}{return_to}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_auth_cookies(response, credentials, app_settings)
    return response


@router.get("/me", response_model=CurrentAuthResponse)
async def me(
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurrentAuthResponse:
    memberships = (
        await session.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == current_auth.user.id)
            .order_by(Workspace.created_at)
        )
    ).all()
    return CurrentAuthResponse(
        user=AuthUserResponse(
            id=current_auth.user.id,
            email=current_auth.user.email,
            display_name=current_auth.user.display_name,
        ),
        workspaces=[
            WorkspaceMembershipResponse(
                id=workspace.id,
                name=workspace.name,
                role=role,
            )
            for workspace, role in memberships
        ],
    )


@router.get("/csrf", response_model=CsrfTokenResponse)
async def csrf_token(
    request: Request,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    try:
        verify_request_origin(request, app_settings)
    except CsrfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        ) from exc
    token = await rotate_csrf_token(session, current_auth.session_id)
    return JSONResponse(
        CsrfTokenResponse(csrf_token=token).model_dump(),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/sessions", response_model=AuthSessionPageResponse)
async def list_sessions(
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=10)] = 5,
) -> JSONResponse:
    current_time = datetime.now(UTC)
    active_filter = (
        AuthSession.user_id == current_auth.user.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.idle_expires_at > current_time,
        AuthSession.absolute_expires_at > current_time,
    )
    total = int(
        await session.scalar(select(func.count()).select_from(AuthSession).where(*active_filter))
        or 0
    )
    rows = (
        await session.scalars(
            select(AuthSession)
            .where(*active_filter)
            .order_by(AuthSession.last_seen_at.desc(), AuthSession.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    total_pages = (total + page_size - 1) // page_size
    payload = AuthSessionPageResponse(
        items=[
            AuthSessionResponse(
                id=row.id,
                device_label=session_device_label(row.user_agent),
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.idle_expires_at,
                is_current=row.id == current_auth.session_id,
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )
    return JSONResponse(payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_session(
    session_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    if session_id == current_auth.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use Sign out to end the current session",
        )
    target = await session.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == current_auth.user.id,
        )
    )
    if target is not None and target.revoked_at is None:
        target.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    await revoke_session(session, current_auth.session_id)
    response = JSONResponse(
        LogoutResponse(
            logout_url=provider.logout_url(app_settings.auth_post_logout_redirect_url)
        ).model_dump()
    )
    clear_auth_cookies(response, app_settings)
    return response
