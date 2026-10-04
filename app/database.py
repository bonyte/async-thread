"""Database engine, session dependency and the shared Redis client."""
from typing import Optional

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import DATABASE_URL

# REVIEW[LOW]: The plan's Stage 0 puts this module in a monorepo-level shared/database.py so all six
# projects share it. Move it there if/when async-thread joins the monorepo.

# REVIEW[MED]: The engine is created at import time, so importing anything from `app` builds a Postgres
# engine. Fix: create it in `lifespan`, store it on `app.state`, and inject it with Depends.
# SQLAlchemy setup
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# REVIEW[MED]: A mutable global that is None until startup; lifespan sets it and tests monkeypatch it.
# Fix: `app.state.redis = ...` in lifespan plus a dependency:
#   def get_redis(request: Request) -> redis.Redis: return request.app.state.redis
# Tests can then use app.dependency_overrides[get_redis], the same way they override get_db.
# Redis setup
redis_client: Optional[redis.Redis] = None


# REVIEW[MED]: This commits after the route returns, but the routes also call commit(), so it's unclear who
# owns the transaction. Fix: the dependency owns it and routes only call flush().
# The return type should be `AsyncIterator[AsyncSession]` (it's a generator).
async def get_db() -> AsyncSession:
    """Dependency: Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
