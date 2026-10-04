"""HTTP routes for threads and messages."""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app import database
from app.database import get_db
from app.models import Thread, ThreadMessage
from app.schemas import (
    CreateThreadRequest,
    MessageResponse,
    SendMessageRequest,
    ThreadMessagesResponse,
    ThreadResponse,
)
from app.services import participant_hash

# REVIEW[MED]: No version prefix. This module becomes "Conversation create" in the SMS service, so use
# `APIRouter(prefix="/v1", tags=["threads"])`. That changes every URL, so update tests and clients with it.
router = APIRouter()


# REVIEW[MED]: status_code=201 is fixed, so returning an existing thread also returns 201.
# Fix: inject `response: Response` and set `response.status_code = 200` when created=False; add a
# Location header. Document errors with `responses={422: ...}` so the OpenAPI spec is complete.
# `response_model=` repeats the `-> ThreadResponse` annotation; keep only one.
@router.post("/threads", response_model=ThreadResponse, status_code=201)
async def create_thread(
    request: CreateThreadRequest,
    # REVIEW[HIGH]: Any caller can act as any user. Fix: `user_id: str = Depends(require_auth)` from the
    # plan's shared/auth.py (Stage 0).
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
    # REVIEW[MED]: db.rollback() below rolls back the WHOLE session, not just this insert. That silently
    # discards any earlier work once this route does more than one write.
    # Fix (option A): only roll back the insert, with a savepoint:
    #   try:
    #       async with db.begin_nested():
    #           db.add(thread)
    #   except IntegrityError:
    #       ...select...
    # Fix (option B): no exception needed, one round-trip, still ORM/Core (no text()):
    #   stmt = postgresql.insert(Thread).values(...).on_conflict_do_nothing().returning(Thread.id)
    # Also remove the commit() here; get_db owns the commit.
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


# REVIEW[MED]: Add `responses={404: ..., 422: ..., 503: ...}` with a standard error body
# `{code, message, retryable}`, so a caller (such as a Lambda worker) can tell retryable failures from final ones.
@router.post("/threads/{thread_id}/messages", response_model=MessageResponse, status_code=201)
async def send_message(
    thread_id: str,  # REVIEW[LOW]: type it as `UUID` so invalid IDs return 422 without a DB lookup.
    request: SendMessageRequest,
    # REVIEW[HIGH]: Same as create_thread; use Depends(require_auth).
    sender_id: str = Query(...),  # In real app, extract from JWT
    db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """
    Send a message to a thread.

    - Verifies thread exists
    - Stores message in RDS
    - Increments Redis unread counter
    """
    # REVIEW[HIGH]: Doesn't check that the sender is a participant. Fix (after the model stores participants):
    #   thread = await db.get(Thread, thread_id)
    #   if thread is None or sender_id not in (thread.user_low, thread.user_high):
    #       raise HTTPException(404)   # 404, not 403, as in Task Relay: don't reveal that the thread exists
    # Verify thread exists
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # REVIEW[HIGH]: No idempotency, so a client retry creates a second message (and later a second SMS).
    # Fix: add a unique constraint on (thread_id, client_message_id) and, on IntegrityError, return the
    # existing message with 200, the same pattern create_thread already uses.
    # Store message
    message = ThreadMessage(
        thread_id=thread_id,
        sender_id=sender_id,
        body=request.body
    )
    db.add(message)
    # REVIEW[LOW]: flush() + commit() + refresh() is redundant. get_db commits, and created_at is already set
    # by the Python default, so refresh() is an extra round-trip. Keep only `await db.flush()`.
    await db.flush()
    await db.commit()
    await db.refresh(message)

    # REVIEW[HIGH]: This Redis write runs after the DB commit. If Redis fails, the client gets a 500 for a
    # message that WAS saved, retries, and creates a duplicate.
    # Fix now: wrap it in try/except, log, and continue (unread counts should be rebuildable from the DB).
    # Fix for Task Relay: write an outbox row in the same transaction, and have a relay publish
    # {message_id} to SQS with MessageGroupId=thread_id and MessageDeduplicationId=message.id.
    # REVIEW[HIGH]: The key is per thread, so this also counts the sender's OWN message as unread.
    # Fix: `unread:{thread_id}:{recipient_id}`. Finding the recipient requires participant columns.
    # Increment Redis unread counter (O(1), no DB query)
    unread_key = f"unread:{thread_id}"
    # Read the client through the module: lifespan sets it (and tests patch it) after this module is imported.
    await database.redis_client.incr(unread_key)

    return MessageResponse(
        id=message.id,
        sender_id=message.sender_id,
        body=message.body,
        created_at=message.created_at.isoformat()
    )


# REVIEW[HIGH]: Takes no reader identity, so anyone can read any thread. Add Depends(require_auth) and
# return 404 for non-participants.
# REVIEW[MED]: A GET with side effects (it resets the unread count below). Link previews, retries and
# prefetching will clear unread counts. Make this a pure read and add:
#   POST /threads/{thread_id}/read   body: {up_to_sequence: int}  -> 204
# REVIEW[MED]: Replace page/page_size with `?after=<sequence>&limit=` (keyset pagination).
@router.get("/threads/{thread_id}/messages", response_model=ThreadMessagesResponse)
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
    # REVIEW[LOW]: `await db.get(Thread, thread_id)` is simpler (and is what the plan specifies).
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # REVIEW[MED]: COUNT(*) on every page fetch is O(messages in thread), which contradicts the README's
    # "O(1)" claim. It also runs as a separate query, so `total` can disagree with the page returned.
    # Drop it in favour of next_cursor.
    # Get total count
    count_result = await db.execute(
        select(func.count(ThreadMessage.id)).where(
            ThreadMessage.thread_id == thread_id,
            # REVIEW[MED]: This hides the message from BOTH participants, but the column means "deleted by
            # sender", and a delivered SMS can't be recalled. It should hide the message from the sender only:
            #   or_(ThreadMessage.sender_id != reader_id, ThreadMessage.is_deleted_by_sender.is_(False))
            # REVIEW[LOW]: `== False` gets flagged by linters (E712); use `.is_(False)`.
            ThreadMessage.is_deleted_by_sender == False
        )
    )
    total = count_result.scalar()

    # REVIEW[MED]: Ordering by created_at alone means equal timestamps come back in an undefined order,
    # and Postgres now() returns the TRANSACTION start time, so messages saved in one transaction tie.
    # Offset pagination also shifts when messages are hidden and gets slower deeper into a thread.
    # Fix: .where(ThreadMessage.sequence > after).order_by(ThreadMessage.sequence).limit(limit)
    # with an index on (thread_id, sequence).
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

    # REVIEW[HIGH]: Lost update. A message sent between the SELECT above and this SET has its count
    # wiped without ever being shown. Also:
    # - Reading ANY page (even the oldest) clears the count.
    # - One participant reading clears the other participant's count.
    # Fix: store a read pointer per participant in the DB (last_read_sequence), set it in
    # POST /threads/{id}/read, compute unread = latest_sequence - last_read_sequence, and use Redis
    # only as a cache.
    # Reset Redis unread counter on read
    unread_key = f"unread:{thread_id}"
    await database.redis_client.set(unread_key, "0")

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
