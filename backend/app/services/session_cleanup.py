from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession


async def cleanup_expired_sessions(
    session: AsyncSession,
    *,
    current_time: datetime | None = None,
    limit: int = 100,
) -> int:
    now = current_time or datetime.now(UTC)
    expired_ids = list(
        (
            await session.scalars(
                select(AuthSession.id)
                .where(
                    or_(
                        AuthSession.revoked_at.is_not(None),
                        AuthSession.idle_expires_at <= now,
                        AuthSession.absolute_expires_at <= now,
                    )
                )
                .order_by(AuthSession.absolute_expires_at, AuthSession.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not expired_ids:
        await session.rollback()
        return 0
    await session.execute(delete(AuthSession).where(AuthSession.id.in_(expired_ids)))
    await session.commit()
    return len(expired_ids)
