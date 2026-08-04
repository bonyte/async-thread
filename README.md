# Async Thread - Project 5

**Proves:** Participant hash + IntegrityError retry under load + Redis unread counts

## Overview

The Async Thread project demonstrates three critical patterns:

1. **Participant Hash**: Order-independent conversation deduplication
2. **IntegrityError Retry**: Race condition handling using unique constraints
3. **Redis Unread Counts**: O(1) counter increments without database queries

## Architecture

### Database Models

#### Thread
- `id`: UUID primary key
- `participant_hash`: SHA-256 of sorted participant IDs (unique constraint)
- `messages`: Relationship to ThreadMessage ordered by created_at

The unique constraint on `participant_hash` ensures only one conversation exists between any two participants, regardless of who initiates or operation order.

#### ThreadMessage
- `id`: UUID primary key
- `thread_id`: Foreign key to Thread
- `sender_id`: User sending the message
- `body`: Message content
- `is_deleted_by_sender`: Soft-delete flag
- `created_at`: Timestamp

### Key Functions

#### `participant_hash(id_a: str, id_b: str) -> str`
```python
sorted_ids = sorted([id_a, id_b])
combined = "|".join(sorted_ids)
return hashlib.sha256(combined.encode()).hexdigest()
```
Order-independent: `participant_hash("A", "B") == participant_hash("B", "A")`

### Routes

#### POST /threads
Create or retrieve a thread using **optimistic insert + retry pattern**:
```
try:
  INSERT thread with participant_hash
  RETURN {id, created: true}
except IntegrityError:
  ROLLBACK
  SELECT thread where participant_hash == hash
  RETURN {id, created: false}
```

Under load balancing, two concurrent requests for the same pair always resolve to one thread:
- First request wins the unique constraint, inserts successfully
- Second request gets IntegrityError, retries with SELECT, gets same thread_id
- Result: distributed lock achieved without explicit locking

**Status**: 201 Created or 200 OK
**Response**: `{id: str, created: bool}`

#### POST /threads/{thread_id}/messages
Send a message to a thread:
- Verify thread exists (404 if not)
- Store message in RDS
- **INCR unread:{thread_id}** in Redis (O(1), no DB query)

**Status**: 201 Created
**Response**: `{id: str, sender_id: str, body: str, created_at: str}`

#### GET /threads/{thread_id}/messages
Read thread messages with pagination:
- Exclude soft-deleted messages (is_deleted_by_sender == false)
- Return paginated results with total count
- **SET unread:{thread_id} 0** on read

**Query Parameters**:
- `page`: Page number (default 1)
- `page_size`: Results per page (default 50, max 100)

**Response**:
```json
{
  "messages": [...],
  "total": 42,
  "page": 1
}
```

## Core Patterns

### 1. Race Condition Handling via Unique Constraint

Instead of pessimistic locking, use the database constraint as a distributed lock:

```python
try:
    thread = Thread(participant_hash=phash)
    db.add(thread)
    await db.flush()
    return {"id": thread.id, "created": True}
except IntegrityError:
    await db.rollback()
    result = await db.execute(
        select(Thread).where(Thread.participant_hash == phash)
    )
    thread = result.scalar_one()
    return {"id": thread.id, "created": False}
```

**Why it works under load balancing**:
- Two instances attempt INSERT simultaneously
- Database enforces unique constraint at transaction commit time
- One succeeds, one fails with IntegrityError
- Losing instance rolls back and retrieves the winner's row
- Both return same thread_id

**Benefit**: No explicit locks, no deadlock risk, fully deterministic.

### 2. Redis Unread Counter (O(1))

Traditional approach (wrong):
```
for each new message:
  SELECT COUNT(*) FROM messages WHERE thread_id == id AND read_at IS NULL
  → Scale issue: every read requires DB query
```

Our approach:
```
on message send:    INCR unread:{thread_id}       # O(1)
on thread read:     SET unread:{thread_id} 0      # O(1)
poll current count: GET unread:{thread_id}        # O(1)
```

No database query, atomic at Redis level.

### 3. Soft Deletes + Append-Only

Messages support soft-delete without modifying history:
```python
is_deleted_by_sender: bool = False
```

Queries always filter: `is_deleted_by_sender == False`

Benefits:
- Audit trail preserved (message still in DB)
- No actual row deletion (no lock contention)
- Can support "undo delete" (flip flag back to False)

## Installation & Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 15 (or SQLite for dev)
- Redis 7

### Development Setup
```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname
export REDIS_URL=redis://:redispassword@localhost:6379
```

### Run Application
```bash
# Start server
uvicorn main:app --reload --port 8005

# Health check
curl http://localhost:8005/health
```

### Run Tests
```bash
# All tests with coverage
pytest test_async_thread.py -v --cov=main --cov-report=term-missing

# Specific test
pytest test_async_thread.py::test_participant_hash_is_order_independent -v
```

## Verification

### Test Coverage
```
test_participant_hash_is_order_independent
  ✓ Hash(A,B) == Hash(B,A)

test_same_pair_resolves_to_same_thread
  ✓ Unique constraint prevents duplicate threads

test_create_thread_returns_same_id_on_race
  ✓ Both requests get same thread_id despite race

test_message_ordered_by_created_at
  ✓ Messages returned in insertion order

test_redis_unread_increments_on_send
  ✓ INCR unread:{thread_id} on message send

test_redis_unread_resets_on_read
  ✓ SET unread:{thread_id} 0 on thread read

test_deleted_message_excluded_from_results
  ✓ Soft-deleted messages not returned

test_thread_not_found_returns_404
  ✓ 404 on non-existent thread

test_send_to_nonexistent_thread_returns_404
  ✓ 404 when sending to missing thread
```

### Manual Verification

```bash
# Get JWT token
TOKEN=$(python -c "
import sys; sys.path.insert(0,'../');
from shared.auth import make_token; print(make_token('user-aaa'))
")

# Create thread with user-bbb
curl -X POST http://localhost:8005/threads?sender_id=user-aaa \
  -H "Content-Type: application/json" \
  -d '{"with_user_id": "user-bbb"}' \
  | python -m json.tool

# Reverse order — should get same thread
curl -X POST http://localhost:8005/threads?sender_id=user-bbb \
  -H "Content-Type: application/json" \
  -d '{"with_user_id": "user-aaa"}' \
  | python -m json.tool
# Expected: same id, created: false

# Send messages
curl -X POST http://localhost:8005/threads/{thread_id}/messages?sender_id=user-aaa \
  -H "Content-Type: application/json" \
  -d '{"body": "Hello"}' \
  | python -m json.tool

# Read thread
curl http://localhost:8005/threads/{thread_id}/messages \
  | python -m json.tool
# Expected: messages in order, unread counter reset
```

## Database Schema

```sql
-- Threads: Unique by participant pair
CREATE TABLE threads (
    id VARCHAR(36) PRIMARY KEY,
    participant_hash VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_threads_participant_hash ON threads(participant_hash);

-- Thread Messages: Append-only with soft-delete
CREATE TABLE thread_messages (
    id VARCHAR(36) PRIMARY KEY,
    thread_id VARCHAR(36) NOT NULL REFERENCES threads(id),
    sender_id VARCHAR(36) NOT NULL,
    body VARCHAR(2000) NOT NULL,
    is_deleted_by_sender BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_thread_messages_thread_id ON thread_messages(thread_id);
CREATE INDEX idx_thread_messages_sender_id ON thread_messages(sender_id);
```

## Security Considerations

1. **Participant Hash**: Not reversible to user IDs (SHA-256)
2. **Soft Deletes**: Prevents accidental data loss
3. **Read-Only Verification**: GET operations never modify state
4. **Redis Unread**: Counter isolated per thread, not globally accessible

## Carries Into SMS Protocol

This project's patterns enable the SMS delivery system's core messaging layer:

| Pattern | SMS Application |
|---------|-----------------|
| Participant Hash | Conversation dedup: customer + agent |
| IntegrityError Retry | Handle concurrent conversation creation |
| Redis Unread | Notification queue without DB scalability hit |

## No Raw SQL Guarantee

All database access uses SQLAlchemy ORM only:
- ✅ `select(Thread).where(...)`
- ✅ `update(ThreadMessage).where(...).values(...)`
- ✅ `select(func.count(ThreadMessage.id))`
- ❌ `db.execute(text("SELECT ..."))`
- ❌ Raw string SQL anywhere

## File Structure

```
async-thread/
├── main.py                 # FastAPI app, routes
├── models.py               # Thread, ThreadMessage ORM models
├── conftest.py             # Pytest fixtures and test setup
├── test_async_thread.py    # Test suite
├── requirements.txt        # Dependencies
└── README.md              # This file
```

## Next Steps

This project feeds into **Project 6 — Task Relay**, which builds messaging distribution via SQS/Lambda.

The conversation patterns established here become delivery workflows in the final SMS Protocol Replication Service.
