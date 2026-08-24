import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import Settings
from app.models import AuthSession, MemberRole, User, Workspace, WorkspaceMember
from app.services.identity_provider import VerifiedIdentity

LAST_SEEN_UPDATE_INTERVAL = timedelta(minutes=5)
MAX_USER_AGENT_LENGTH = 1000


class AuthenticationRequired(Exception):
    pass


class AccountLinkRequired(Exception):
    pass


class AccessRestricted(Exception):
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


def csrf_token_for_session(raw_session_token: str) -> str:
    return hashlib.sha256(
        b"cassist-csrf-v1\0" + raw_session_token.encode("utf-8")
    ).hexdigest()


def is_allowed_user_email(email: str, settings: Settings) -> bool:
    return email.strip().casefold() in settings.auth_allowed_emails


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
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[User, SessionCredentials]:
    if not is_allowed_user_email(identity.email, settings):
        raise AccessRestricted("This account is not allowed to use CAssist")

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

        # Serialize logins for this user so concurrent callbacks cannot exceed
        # the configured active-session limit.
        await session.execute(select(User.id).where(User.id == user.id).with_for_update())

        await session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                (
                    (AuthSession.idle_expires_at <= current_time)
                    | (AuthSession.absolute_expires_at <= current_time)
                ),
            )
            .values(revoked_at=current_time)
        )

        active_session_ids = list(
            (
                await session.scalars(
                    select(AuthSession.id)
                    .where(
                        AuthSession.user_id == user.id,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.idle_expires_at > current_time,
                        AuthSession.absolute_expires_at > current_time,
                    )
                    .order_by(
                        AuthSession.last_seen_at.asc(),
                        AuthSession.created_at.asc(),
                        AuthSession.id.asc(),
                    )
                )
            ).all()
        )
        sessions_to_revoke = len(active_session_ids) - settings.auth_max_active_sessions + 1
        if sessions_to_revoke > 0:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id.in_(active_session_ids[:sessions_to_revoke]))
                .values(revoked_at=current_time)
            )

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
                user_agent=(user_agent or "").strip()[:MAX_USER_AGENT_LENGTH] or None,
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


def session_device_label(user_agent: str | None) -> str:
    value = user_agent or ""
    if "Edg/" in value:
        browser = "Edge"
    elif "Firefox/" in value:
        browser = "Firefox"
    elif "Chrome/" in value or "CriOS/" in value:
        browser = "Chrome"
    elif "Safari/" in value and "Version/" in value:
        browser = "Safari"
    else:
        browser = "Unknown browser"

    if "iPhone" in value:
        device = "iPhone"
    elif "iPad" in value:
        device = "iPad"
    elif "Android" in value:
        device = "Android"
    elif "Windows" in value:
        device = "Windows"
    elif "Macintosh" in value or "Mac OS X" in value:
        device = "macOS"
    elif "Linux" in value:
        device = "Linux"
    else:
        device = "Unknown device"

    if browser == "Unknown browser" and device == "Unknown device":
        return device
    return f"{browser} on {device}"


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
    if not is_allowed_user_email(user.email, settings):
        if auth_session.revoked_at is None:
            auth_session.revoked_at = current_time
            await session.commit()
        raise AccessRestricted("This account is not allowed to use CAssist")
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
    raw_session_token = request.cookies.get(settings.auth_session_cookie_name)
    if not header_token or not raw_session_token:
        raise CsrfValidationError("CSRF validation failed")
    expected_token = csrf_token_for_session(raw_session_token)
    if not secrets.compare_digest(header_token, expected_token):
        raise CsrfValidationError("CSRF validation failed")


def verify_request_origin(request: Request, settings: Settings) -> None:
    if request.headers.get("origin") not in settings.cors_origins:
        raise CsrfValidationError("CSRF validation failed")


async def revoke_session(
    session: AsyncSession,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = now or datetime.now(UTC)
        await session.commit()
