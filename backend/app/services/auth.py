import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import Settings
from app.models import AuthSession, MemberRole, User, Workspace, WorkspaceMember
from app.services.identity_provider import VerifiedIdentity

LAST_SEEN_UPDATE_INTERVAL = timedelta(minutes=5)


class AuthenticationRequired(Exception):
    pass


class AccountLinkRequired(Exception):
    pass


class CsrfValidationError(Exception):
    pass


@dataclass(frozen=True)
class SessionCredentials:
    session_token: str
    absolute_expires_at: datetime


@dataclass(frozen=True)
class CurrentAuth:
    session_id: UUID
    user: User
    csrf_token_hash: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def external_auth_id(identity: VerifiedIdentity) -> str:
    return json.dumps(
        [identity.issuer.rstrip("/"), identity.subject],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def validate_return_to(value: str | None) -> str:
    if not value:
        return "/"
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("return_to must be a relative application path")
    return value


async def establish_session(
    session: AsyncSession,
    identity: VerifiedIdentity,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[User, SessionCredentials]:
    current_time = now or datetime.now(UTC)
    auth_id = external_auth_id(identity)

    try:
        user = await session.scalar(select(User).where(User.external_auth_id == auth_id))
        email_owner = await session.scalar(
            select(User).where(func.lower(User.email) == identity.email.lower())
        )
        if email_owner is not None and (user is None or email_owner.id != user.id):
            raise AccountLinkRequired("The verified email belongs to another identity")

        if user is None:
            user = User(
                external_auth_id=auth_id,
                email=identity.email,
                display_name=identity.display_name,
                last_seen_at=current_time,
            )
            session.add(user)
            await session.flush()
        else:
            user.email = identity.email
            user.display_name = identity.display_name
            user.last_seen_at = current_time

        membership = await session.scalar(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user.id).limit(1)
        )
        if membership is None:
            workspace = Workspace(name="My workspace", created_by_user_id=user.id)
            session.add(workspace)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=MemberRole.OWNER,
                )
            )

        session_token = create_opaque_token()
        absolute_expires_at = current_time + timedelta(
            seconds=settings.auth_session_absolute_seconds
        )
        idle_expires_at = min(
            current_time + timedelta(seconds=settings.auth_session_idle_seconds),
            absolute_expires_at,
        )
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=hash_token(session_token),
                csrf_token_hash=hash_token(create_opaque_token()),
                last_seen_at=current_time,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
            )
        )
        await session.commit()
    except AccountLinkRequired:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise AccountLinkRequired("The identity could not be linked safely") from exc

    return user, SessionCredentials(
        session_token=session_token,
        absolute_expires_at=absolute_expires_at,
    )


async def resolve_session(
    session: AsyncSession,
    raw_token: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> CurrentAuth:
    if not raw_token:
        raise AuthenticationRequired("Authentication is required")

    current_time = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == hash_token(raw_token))
        )
    ).one_or_none()
    if row is None:
        raise AuthenticationRequired("Authentication is required")

    auth_session, user = row
    if (
        auth_session.revoked_at is not None
        or auth_session.idle_expires_at <= current_time
        or auth_session.absolute_expires_at <= current_time
    ):
        if auth_session.revoked_at is None:
            auth_session.revoked_at = current_time
            await session.commit()
        raise AuthenticationRequired("Authentication is required")

    if auth_session.last_seen_at <= current_time - LAST_SEEN_UPDATE_INTERVAL:
        auth_session.last_seen_at = current_time
        auth_session.idle_expires_at = min(
            current_time + timedelta(seconds=settings.auth_session_idle_seconds),
            auth_session.absolute_expires_at,
        )
        user.last_seen_at = current_time
        await session.commit()

    return CurrentAuth(
        session_id=auth_session.id,
        user=user,
        csrf_token_hash=auth_session.csrf_token_hash,
    )


def verify_csrf(request: Request, current_auth: CurrentAuth, settings: Settings) -> None:
    verify_request_origin(request, settings)
    header_token = request.headers.get("x-csrf-token")
    if not header_token:
        raise CsrfValidationError("CSRF validation failed")
    if not secrets.compare_digest(hash_token(header_token), current_auth.csrf_token_hash):
        raise CsrfValidationError("CSRF validation failed")


def verify_request_origin(request: Request, settings: Settings) -> None:
    if request.headers.get("origin") not in settings.cors_origins:
        raise CsrfValidationError("CSRF validation failed")


async def rotate_csrf_token(session: AsyncSession, session_id: UUID) -> str:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        raise AuthenticationRequired("Authentication is required")
    csrf_token = create_opaque_token()
    auth_session.csrf_token_hash = hash_token(csrf_token)
    await session.commit()
    return csrf_token


async def revoke_session(
    session: AsyncSession,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = now or datetime.now(UTC)
        await session.commit()
