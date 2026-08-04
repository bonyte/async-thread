# Async Thread - Quick Start Guide

## 5-Minute Setup

### 1. Install Dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run Verification
```bash
python verify.py
```

Expected output:
```
✅ All checks passed!

🚀 Quick start:
  1. Start the app: uvicorn main:app --reload --port 8005
  2. Run tests: pytest test_async_thread.py -v
  3. Check health: curl http://localhost:8005/health
```

### 3. Start the Server
```bash
uvicorn main:app --reload --port 8005
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8005
INFO:     Application startup complete
```

### 4. Run Tests (in another terminal)
```bash
pytest test_async_thread.py -v --cov=main
```

Expected output:
```
test_async_thread.py::test_participant_hash_is_order_independent PASSED
test_async_thread.py::test_same_pair_resolves_to_same_thread PASSED
test_async_thread.py::test_create_thread_returns_same_id_on_race PASSED
test_async_thread.py::test_message_ordered_by_created_at PASSED
test_async_thread.py::test_redis_unread_increments_on_send PASSED
test_async_thread.py::test_redis_unread_resets_on_read PASSED
test_async_thread.py::test_deleted_message_excluded_from_results PASSED
test_async_thread.py::test_thread_not_found_returns_404 PASSED
test_async_thread.py::test_send_to_nonexistent_thread_returns_404 PASSED

========================== 9 passed in 0.12s ==========================
```

## What Each Component Does

### `main.py` — FastAPI Application (295 lines)
REST API server with three routes:

```
POST /threads                     Create/retrieve thread
POST /threads/{id}/messages       Send message
GET /threads/{id}/messages        Read messages
```

**Key patterns**:
- Optimistic insert with IntegrityError retry
- O(1) Redis counters
- Async all the way

### `models.py` — SQLAlchemy ORM (85 lines)
Database models without raw SQL:

```
Thread               One per unique participant pair
  ├─ id: UUID
  ├─ participant_hash: SHA-256 unique index
  └─ messages: [ThreadMessage, ...]

ThreadMessage        Append-only, soft-delete capable
  ├─ id: UUID
  ├─ thread_id: foreign key
  ├─ sender_id: indexed
  ├─ body: message text
  ├─ is_deleted_by_sender: soft-delete flag
  └─ created_at: auto-timestamp
```

### `test_async_thread.py` — Test Suite (250 lines)
9 comprehensive test cases covering:
- Order-independent hashing
- Race condition handling
- Message ordering
- Redis counter behavior
- Soft deletes
- Error cases

### `conftest.py` — Pytest Fixtures (50 lines)
Test infrastructure:
- In-memory SQLite database
- Fake Redis (no network, atomic)
- AsyncClient with dependency mocking

## Testing Individual Features

### Test 1: Order-Independent Hash
```bash
pytest test_async_thread.py::test_participant_hash_is_order_independent -v
```

### Test 2: Race Condition Handling
```bash
pytest test_async_thread.py::test_create_thread_returns_same_id_on_race -v
```

### Test 3: Redis Counter
```bash
pytest test_async_thread.py::test_redis_unread_increments_on_send -v
pytest test_async_thread.py::test_redis_unread_resets_on_read -v
```

### Test 4: Message Ordering
```bash
pytest test_async_thread.py::test_message_ordered_by_created_at -v
```

### Test 5: Soft Deletes
```bash
pytest test_async_thread.py::test_deleted_message_excluded_from_results -v
```

## Manual API Testing

### Create a Thread
```bash
curl -X POST http://localhost:8005/threads?sender_id=user-alice \
  -H "Content-Type: application/json" \
  -d '{"with_user_id": "user-bob"}' \
  | python -m json.tool
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "created": true
}
```

### Create Same Thread (Other Direction)
```bash
curl -X POST http://localhost:8005/threads?sender_id=user-bob \
  -H "Content-Type: application/json" \
  -d '{"with_user_id": "user-alice"}' \
  | python -m json.tool
```

Response (same ID, created: false):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "created": false
}
```

### Send a Message
```bash
THREAD_ID="550e8400-e29b-41d4-a716-446655440000"

curl -X POST http://localhost:8005/threads/$THREAD_ID/messages?sender_id=user-alice \
  -H "Content-Type: application/json" \
  -d '{"body": "Hey Bob, how are you?"}' \
  | python -m json.tool
```

Response:
```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "sender_id": "user-alice",
  "body": "Hey Bob, how are you?",
  "created_at": "2025-05-21T00:37:36.524000"
}
```

### Send Another Message
```bash
curl -X POST http://localhost:8005/threads/$THREAD_ID/messages?sender_id=user-bob \
  -H "Content-Type: application/json" \
  -d '{"body": "Doing great! You?"}' \
  | python -m json.tool
```

### Read Thread Messages
```bash
curl http://localhost:8005/threads/$THREAD_ID/messages \
  | python -m json.tool
```

Response:
```json
{
  "messages": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "sender_id": "user-alice",
      "body": "Hey Bob, how are you?",
      "created_at": "2025-05-21T00:37:36.524000"
    },
    {
      "id": "770e8400-e29b-41d4-a716-446655440002",
      "sender_id": "user-bob",
      "body": "Doing great! You?",
      "created_at": "2025-05-21T00:37:37.524000"
    }
  ],
  "total": 2,
  "page": 1
}
```

### Health Check
```bash
curl http://localhost:8005/health
```

Response:
```json
{
  "status": "healthy"
}
```

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│         FastAPI Application                 │
│  (main.py - 8005 port)                      │
└──────────────┬──────────────────────────────┘
               │
         ┌─────┴──────┬──────────────┐
         │            │              │
    ┌────▼─┐     ┌────▼─┐    ┌─────▼────┐
    │ POST │     │ POST │    │   GET    │
    │ /thr │     │ /msgs│    │  /msgs   │
    │ eads │     │      │    │          │
    └────┬─┘     └────┬─┘    └────┬─────┘
         │            │            │
         └─────┬──────┴────────────┘
               │
        ┌──────┴──────┬───────────┐
        │             │           │
    ┌───▼──┐    ┌────▼────┐  ┌──▼────┐
    │  RDS │    │  Redis  │  │Checks │
    │(SQL) │    │(Counters)  │ health
    └──────┘    └─────────┘  └───────┘

Models:         Routes:              Data:
━━━━━━━━━━━━━   ━━━━━━━━━━━━━━━━━━   ━━━━━━━━━━━
Thread          POST /threads        PostgreSQL
ThreadMessage   POST /.../messages   Or SQLite
                GET /.../messages    Redis
```

## File Organization

```
async-thread/
│
├── main.py                 # FastAPI app & routes
├── models.py               # SQLAlchemy ORM models
├── conftest.py             # Pytest fixtures
├── test_async_thread.py    # Test suite
│
├── requirements.txt        # Python dependencies
├── verify.py               # Verification script
├── .gitignore              # Git ignore
│
├── README.md               # Full documentation
├── IMPLEMENTATION.md       # Implementation details
└── QUICKSTART.md          # This file
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'fastapi'`
```bash
pip install -r requirements.txt
```

### `Connection refused` on Redis
Redis is optional for dev testing (uses fakeredis). If you want real Redis:
```bash
# macOS
brew install redis
redis-server

# Docker
docker run -d -p 6379:6379 redis:7-alpine
```

### Tests are slow
Tests use in-memory SQLite, should complete in <1 second. If slower, check:
```bash
# See what's running
ps aux | grep python

# Kill any leftover processes
pkill -f uvicorn
```

### Port 8005 already in use
```bash
# Use different port
uvicorn main:app --reload --port 8006

# Or find what's using it
lsof -i :8005  # macOS/Linux
netstat -ano | findstr :8005  # Windows
```

## Next Steps

1. **Review the code**: Start with `models.py` to understand data structure
2. **Understand patterns**: Read `README.md` section "Core Patterns"
3. **Run tests**: `pytest test_async_thread.py -v` to verify everything
4. **Try API**: Use curl examples above to interact with server
5. **Modify routes**: Add new features and test them
6. **Deploy**: Follow production setup in README.md

## Key Takeaways

| Concept | Pattern | Benefit |
|---------|---------|---------|
| **Participant Hash** | SHA-256(sorted IDs) | Order-independent dedup |
| **IntegrityError Retry** | Unique constraint + SELECT | Distributed lock, no deadlock |
| **Redis Counter** | INCR/SET on events | O(1) operations, no DB load |
| **Soft Delete** | Boolean flag + query filter | Audit trail, no data loss |
| **Append-Only** | INSERT only, never UPDATE | No lock contention |

These patterns scale from 1 user to millions of concurrent users without modification.

---

**Ready to start?** Run `python verify.py` to check everything is working!
