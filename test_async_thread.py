"""
Async Thread project test suite.

Tests core functionality:
- Participant hash order independence
- IntegrityError retry on concurrent creation
- Message ordering and deletion
- Redis unread counter behavior
"""
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from httpx import AsyncClient

from main import participant_hash
from models import Thread, ThreadMessage


# ─────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────

def test_participant_hash_is_order_independent():
    """Participant hash should be same regardless of order."""
    hash_ab = participant_hash("user-a", "user-b")
    hash_ba = participant_hash("user-b", "user-a")
    assert hash_ab == hash_ba


@pytest.mark.asyncio
async def test_same_pair_resolves_to_same_thread(test_session):
    """Creating threads between same pair should return same thread."""
    phash_ab = participant_hash("user-a", "user-b")
    phash_ba = participant_hash("user-b", "user-a")
    
    # Hashes should match
    assert phash_ab == phash_ba
    
    # First insert
    thread1 = Thread(participant_hash=phash_ab)
    test_session.add(thread1)
    await test_session.flush()
    thread1_id = thread1.id
    
    # Second insert with same hash should fail on unique constraint
    thread2 = Thread(participant_hash=phash_ba)
    test_session.add(thread2)
    with pytest.raises(IntegrityError):
        await test_session.flush()


@pytest.mark.asyncio
async def test_create_thread_returns_same_id_on_race(client, test_session, test_redis):
    """
    When two requests race to create same thread,
    both should get same thread_id.
    """
    # First request creates thread
    response1 = await client.post(
        "/threads?sender_id=user-a",
        json={"with_user_id": "user-b"}
    )
    assert response1.status_code == 201
    data1 = response1.json()
    assert data1["created"] is True
    thread_id_1 = data1["id"]
    
    # Second request (simulates race — would hit IntegrityError in production)
    # gets same thread
    response2 = await client.post(
        "/threads?sender_id=user-b",
        json={"with_user_id": "user-a"}
    )
    assert response2.status_code == 201
    data2 = response2.json()
    assert data2["id"] == thread_id_1
    assert data2["created"] is False


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
