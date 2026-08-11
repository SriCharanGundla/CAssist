from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_app_settings,
    get_current_auth,
    get_database_session,
    get_identity_provider,
    require_csrf,
)
from app.core.config import Settings
from app.models import Workspace, WorkspaceMember
from app.schemas.auth import (
    AuthUserResponse,
    CsrfTokenResponse,
    CurrentAuthResponse,
    LogoutResponse,
    WorkspaceMembershipResponse,
)
from app.services.auth import (
    AccountLinkRequired,
    CsrfValidationError,
    CurrentAuth,
    SessionCredentials,
    establish_session,
    revoke_session,
    rotate_csrf_token,
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
        _, credentials = await establish_session(session, identity, app_settings)
    except IdentityProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
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
