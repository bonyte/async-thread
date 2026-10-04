"""
Message tests:
- Message ordering and soft deletion
- Redis unread counter behavior
- 404 for unknown threads
"""
import pytest
from sqlalchemy import select

from app.models import Thread, ThreadMessage


# REVIEW[HIGH]: Tests the ORM query copied into the test, not the endpoint (the plan says
# "GET /threads/{id}/messages"). It only passes because the Python-side utcnow default gives each row a
# distinct timestamp. On Postgres with server_default=now(), all 3 rows in this one transaction would tie
# and the order would be undefined. Rewrite it to call the endpoint and assert on `sequence`.
@pytest.mark.asyncio
async def test_message_ordered_by_created_at(test_session):
    """Messages should be returned in insertion order."""
    # Create thread
    thread = Thread(participant_hash="test-hash")
    test_session.add(thread)
    await test_session.flush()

    # Insert messages
    msg1 = ThreadMessage(thread_id=thread.id, sender_id="user-a", body="First")
    msg2 = ThreadMessage(thread_id=thread.id, sender_id="user-b", body="Second")
    msg3 = ThreadMessage(thread_id=thread.id, sender_id="user-a", body="Third")

    test_session.add_all([msg1, msg2, msg3])
    await test_session.flush()
    await test_session.commit()

    # Retrieve in order
    result = await test_session.execute(
        select(ThreadMessage)
        .where(ThreadMessage.thread_id == thread.id)
        .order_by(ThreadMessage.created_at.asc())
    )
    messages = result.scalars().all()

    assert len(messages) == 3
    assert messages[0].body == "First"
    assert messages[1].body == "Second"
    assert messages[2].body == "Third"


@pytest.mark.asyncio
async def test_redis_unread_increments_on_send(client, test_redis):
    """Sending a message should increment Redis unread counter."""
    # Create thread first
    response = await client.post(
        "/threads?sender_id=user-a",
        json={"with_user_id": "user-b"}
    )
    thread_id = response.json()["id"]

    # Send message
    response = await client.post(
        f"/threads/{thread_id}/messages?sender_id=user-a",
        json={"body": "Hello"}
    )
    assert response.status_code == 201

    # Check Redis counter
    unread_key = f"unread:{thread_id}"
    count = await test_redis.get(unread_key)
    assert count == "1"


# REVIEW[MED]: This locks in the behaviour the review flags as a bug (a GET resets a per-thread counter).
# Once unread counts are per user with read pointers, assert instead that:
#   - B's count is 3 after A sends 3,
#   - A's own count stays 0,
#   - POST /read up_to_sequence=2 leaves B at 1.
@pytest.mark.asyncio
async def test_redis_unread_resets_on_read(client, test_redis):
    """Reading messages should reset Redis unread counter to 0."""
    # Create thread
    response = await client.post(
        "/threads?sender_id=user-a",
        json={"with_user_id": "user-b"}
    )
    thread_id = response.json()["id"]

    # Send 3 messages
    for i in range(3):
        await client.post(
            f"/threads/{thread_id}/messages?sender_id=user-a",
            json={"body": f"Message {i}"}
        )

    # Verify counter is 3
    unread_key = f"unread:{thread_id}"
    count = await test_redis.get(unread_key)
    assert count == "3"

    # Read messages
    response = await client.get(f"/threads/{thread_id}/messages")
    assert response.status_code == 200

    # Verify counter is reset
    count = await test_redis.get(unread_key)
    assert count == "0"


# REVIEW[MED]: Also bypasses the endpoint (it repeats the WHERE clause from app/routes.py, so it would pass
# even if the route forgot the filter). Call GET instead, and once reads are identity-aware, assert the message
# is hidden from the SENDER but still visible to the recipient.
@pytest.mark.asyncio
async def test_deleted_message_excluded_from_results(test_session):
    """Soft-deleted messages should not appear in thread messages."""
    # Create thread
    thread = Thread(participant_hash="test-hash")
    test_session.add(thread)
    await test_session.flush()

    # Add messages
    msg1 = ThreadMessage(thread_id=thread.id, sender_id="user-a", body="Keep")
    msg2 = ThreadMessage(thread_id=thread.id, sender_id="user-b", body="Delete")
    msg3 = ThreadMessage(thread_id=thread.id, sender_id="user-a", body="Keep too")

    test_session.add_all([msg1, msg2, msg3])
    await test_session.flush()
    await test_session.commit()

    # Soft-delete msg2
    msg2.is_deleted_by_sender = True
    await test_session.commit()

    # Query non-deleted messages
    result = await test_session.execute(
        select(ThreadMessage)
        .where(
            ThreadMessage.thread_id == thread.id,
            ThreadMessage.is_deleted_by_sender == False
        )
        .order_by(ThreadMessage.created_at.asc())
    )
    messages = result.scalars().all()

    assert len(messages) == 2
    assert messages[0].body == "Keep"
    assert messages[1].body == "Keep too"


@pytest.mark.asyncio
async def test_thread_not_found_returns_404(client):
    """Getting non-existent thread should return 404."""
    response = await client.get("/threads/nonexistent/messages")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_send_to_nonexistent_thread_returns_404(client):
    """Sending to non-existent thread should return 404."""
    response = await client.post(
        "/threads/nonexistent/messages?sender_id=user-a",
        json={"body": "Hello"}
    )
    assert response.status_code == 404
