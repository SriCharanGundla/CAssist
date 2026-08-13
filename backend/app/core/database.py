from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Idempotency may be exercised by ASGI test clients that use a fresh event loop
# per test. A dedicated non-pooling engine is also appropriate for this small,
# short-lived coordination transaction.
idempotency_engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
)
idempotency_session_factory = async_sessionmaker(
    bind=idempotency_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
