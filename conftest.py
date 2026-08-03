"""
Pytest configuration and fixtures for async-thread tests.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import fakeredis.aioredis

from main import app, get_db, Base


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
        await session.close()


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
    
    async def override_get_db():
        yield test_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    # Mock the global redis_client
    import main as main_module
    original_redis = main_module.redis_client
    main_module.redis_client = test_redis
    
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    
    # Restore original
    main_module.redis_client = original_redis
    app.dependency_overrides.clear()
