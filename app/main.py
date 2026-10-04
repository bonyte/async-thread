"""
Async Thread project main application.

Implements:
- POST /threads: Create or find thread (handles IntegrityError race condition)
- POST /threads/{thread_id}/messages: Send message
- GET /threads/{thread_id}/messages: Read thread messages

Run: uvicorn app.main:app --reload --port 8005
"""
# REVIEW: Inline review notes are tagged `REVIEW[HIGH|MED|LOW]`. Search for "REVIEW" to list them.
# Biggest issues, fix in this order:
#   1. Thread doesn't store its participants (app/models.py), which blocks auth, per-user unread counts and an inbox.
#   2. Unread counts live only in Redis and are written separately from the DB (app/routes.py): lost updates,
#      can't be rebuilt.
#   3. Sending has no idempotency, so a client retry creates a duplicate message (a duplicate SMS once Task Relay
#      is wired in).
#   4. Identity comes from a query param; the plan (Stage 0 shared/auth.py) requires JWT.
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from app import database
from app.config import REDIS_URL
from app.models import Base
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    # REVIEW[HIGH]: create_all at startup is not a migration. Multiple instances race to run DDL, and schema
    # changes (participant columns, sequence, client_message_id) can't be applied to existing tables.
    # Fix: Alembic, as the plan's Stage 5.1 requires (`alembic upgrade head` as a deploy step).
    # Startup
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    database.redis_client = await redis.from_url(REDIS_URL, decode_responses=True)

    yield

    # Shutdown
    await database.engine.dispose()
    await database.redis_client.close()  # REVIEW[LOW]: deprecated in redis-py 5; use `await ....aclose()`.


app = FastAPI(title="Async Thread", lifespan=lifespan)
app.include_router(router)


# REVIEW[LOW]: Checks neither Postgres nor Redis. Keep this as a liveness check (is the process running)
# and add `/ready`, which runs `SELECT 1` and `PING` and returns 503 on failure, for load balancers.
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
