"""
Thread creation tests:
- Participant hash order independence
- IntegrityError retry on duplicate creation
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Thread
from app.services import participant_hash


def test_participant_hash_is_order_independent():
    """Participant hash should be same regardless of order."""
    hash_ab = participant_hash("user-a", "user-b")
    hash_ba = participant_hash("user-b", "user-a")
    assert hash_ab == hash_ba


# REVIEW[LOW]: Add a test for the '|' collision (see participant_hash in app/services.py) that asserts
# participant_hash("a|b", "c") != participant_hash("a", "b|c"). It fails today.


# REVIEW[MED]: The name says "resolves to same thread", but this only checks that the DB rejects a duplicate
# hash; it never resolves anything. The plan's version goes through POST /threads twice. Rename it to
# test_duplicate_participant_hash_violates_unique_constraint, or rewrite it to call the endpoint.
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


# REVIEW[HIGH]: Not a race: the two requests run one after the other. It covers the IntegrityError path
# (good), but the concurrency claim is untested. Rename it to
# test_create_thread_returns_existing_on_duplicate and add a real concurrent test (see tests/conftest.py).
# The second assertion should also expect 200 once create_thread returns 200 for existing threads.
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
