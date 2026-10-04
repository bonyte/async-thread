"""
Pytest configuration and fixtures for async-thread tests.
"""
# REVIEW[MED]: Missing tests, in priority order:
#   1. A real concurrency test (tests/test_concurrency.py): asyncio.gather of N POST /threads for the same
#      pair, on Postgres, with separate sessions. Assert exactly one row and that all responses have the same id.
#   2. Auth: a non-participant gets 404 on read and send (once JWT and participant columns exist).
#   3. Idempotent send: the same client_message_id twice gives one row, and the second response is 200.
#   4. Pagination: cursor boundaries and limit, through the endpoint.
#   5. Validation: an empty or oversized body gives 422; a thread with yourself gives 422.
#   6. Redis down on send: the message is saved and the response is still 2xx (the counter can be rebuilt).
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import fakeredis.aioredis

from app import database
from app.database import get_db
from app.main import app
from app.models import Base

# REVIEW[HIGH]: SQLite can't test the claims this project exists to prove. It serializes all writes (so the
# race never happens), ignores String(N) limits, and its now() behaves differently from Postgres.
# Fix: run against the Postgres container from the plan's Stage 0 docker-compose, using
# TEST_DATABASE_URL from env and a fresh schema per test session. Keep SQLite only for pure unit tests,
# if you need it at all.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db_engine():
    """Create test database engine."""
    test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_db_engine):
    """Create test database session."""
    test_session_maker = async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with test_session_maker() as session:
        yield session
        await session.close()  # REVIEW[LOW]: redundant; `async with` already closes the session.


@pytest_asyncio.fixture
async def test_redis():
    """Create fake Redis client for testing that returns strings.

    Use decode_responses=True so Redis get() returns str not bytes, keeping
    test expectations consistent across all test cases.
    """
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture
async def client(test_db_engine, test_session, test_redis):
    """Create test client with mocked dependencies."""
    from httpx import AsyncClient

    # REVIEW[MED]: The app and the test share ONE session, so the commit/rollback behaviour of get_db is
    # never exercised, and a rollback inside a route also rolls back the test's own setup.
    # Fix: give each request its own session from the test engine (mirroring get_db), and give each test a
    # separate session for setup and assertions.
    async def override_get_db():
        yield test_session

    # REVIEW[MED]: Patching a module global is fragile. Once app/database.py has a get_redis dependency,
    # use app.dependency_overrides[get_redis].
    app.dependency_overrides[get_db] = override_get_db

    # Mock the global redis_client
    original_redis = database.redis_client
    database.redis_client = test_redis

    # REVIEW[LOW]: `app=` is deprecated in httpx; use `AsyncClient(transport=ASGITransport(app=app), ...)`.
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    # Restore original
    database.redis_client = original_redis
    app.dependency_overrides.clear()
