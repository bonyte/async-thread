"""
Async Thread project main application.

Implements:
- POST /threads: Create or find thread (handles IntegrityError race condition)
- POST /threads/{thread_id}/messages: Send message
- GET /threads/{thread_id}/messages: Read thread messages
"""
import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, String
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
import redis.asyncio as redis

from models import Base, Thread, ThreadMessage


# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/dbname"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://:redispassword@localhost:6379")

# SQLAlchemy setup
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# Redis setup
redis_client: Optional[redis.Redis] = None


# ─────────────────────────────────────────────────────────────────
# Lifespan context
# ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    global redis_client
    
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
    
    yield
    
    # Shutdown
    await engine.dispose()
    await redis_client.close()


# ─────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Async Thread", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    """Dependency: Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ─────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────

def participant_hash(id_a: str, id_b: str) -> str:
    """
    Compute deterministic hash of two participant IDs.
    
    Returns SHA-256 of '|'.join(sorted([id_a, id_b]))
    Order-independent: (A,B) and (B,A) produce same hash.
    """
    sorted_ids = sorted([id_a, id_b])
    combined = "|".join(sorted_ids)
    return hashlib.sha256(combined.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────
# Request/Response models
# ─────────────────────────────────────────────────────────────────

class CreateThreadRequest(BaseModel):
    """Request body for creating a thread."""
    with_user_id: str


class ThreadResponse(BaseModel):
    """Response body for thread creation."""
    id: str
    created: bool


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""
    body: str


class MessageResponse(BaseModel):
    """Response body for message details."""
    id: str
    sender_id: str
    body: str
    created_at: str


class ThreadMessagesResponse(BaseModel):
    """Response body for reading thread messages."""
    messages: list[MessageResponse]
    total: int
    page: int


# ─────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────

@app.post("/threads", response_model=ThreadResponse, status_code=201)
async def create_thread(
    request: CreateThreadRequest,
    sender_id: str = Query(...),  # In real app, extract from JWT
    db: AsyncSession = Depends(get_db)
) -> ThreadResponse:
    """
    Create or retrieve a thread with another user.
    
    Demonstrates IntegrityError retry pattern:
    - Attempt optimistic INSERT
    - On unique constraint violation, retry with SELECT
    - Race condition always resolves to same thread under load balancing
    """
    phash = participant_hash(sender_id, request.with_user_id)
    
    # OPTIMISTIC INSERT
    try:
        thread = Thread(participant_hash=phash)
        db.add(thread)
        await db.flush()  # assign id without committing
        await db.commit()
        return ThreadResponse(id=thread.id, created=True)
    
    except IntegrityError:
        # Race condition: another instance inserted simultaneously
        # The unique constraint caught it
        await db.rollback()
        
        # Retry with SELECT
        result = await db.execute(
            select(Thread).where(Thread.participant_hash == phash)
        )
        thread = result.scalar_one()
        return ThreadResponse(id=thread.id, created=False)


@app.post("/threads/{thread_id}/messages", response_model=MessageResponse, status_code=201)
async def send_message(
    thread_id: str,
    request: SendMessageRequest,
    sender_id: str = Query(...),  # In real app, extract from JWT
    db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """
    Send a message to a thread.
    
    - Verifies thread exists
    - Stores message in RDS
    - Increments Redis unread counter
    """
    # Verify thread exists
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    # Store message
    message = ThreadMessage(
        thread_id=thread_id,
        sender_id=sender_id,
        body=request.body
    )
    db.add(message)
    await db.flush()
    await db.commit()
    await db.refresh(message)
    
    # Increment Redis unread counter (O(1), no DB query)
    unread_key = f"unread:{thread_id}"
    await redis_client.incr(unread_key)
    
    return MessageResponse(
        id=message.id,
        sender_id=message.sender_id,
        body=message.body,
        created_at=message.created_at.isoformat()
    )


@app.get("/threads/{thread_id}/messages", response_model=ThreadMessagesResponse)
async def read_thread_messages(
    thread_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
) -> ThreadMessagesResponse:
    """
    Read messages from a thread with pagination.
    
    - Excludes deleted messages
    - Resets Redis unread counter on read
    - Returns total count and current page
    """
    # Verify thread exists
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    # Get total count
    count_result = await db.execute(
        select(func.count(ThreadMessage.id)).where(
            ThreadMessage.thread_id == thread_id,
            ThreadMessage.is_deleted_by_sender == False
        )
    )
    total = count_result.scalar()
    
    # Get paginated messages
    offset = (page - 1) * page_size
    result = await db.execute(
        select(ThreadMessage)
        .where(
            ThreadMessage.thread_id == thread_id,
            ThreadMessage.is_deleted_by_sender == False
        )
        .order_by(ThreadMessage.created_at.asc())
        .offset(offset)
        .limit(page_size)
    )
    messages = result.scalars().all()
    
    # Reset Redis unread counter on read
    unread_key = f"unread:{thread_id}"
    await redis_client.set(unread_key, "0")
    
    return ThreadMessagesResponse(
        messages=[
            MessageResponse(
                id=msg.id,
                sender_id=msg.sender_id,
                body=msg.body,
                created_at=msg.created_at.isoformat()
            )
            for msg in messages
        ],
        total=total,
        page=page
    )


# ─────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
