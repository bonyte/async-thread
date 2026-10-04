# Async Thread (Project 5)

Two-person conversation threads on FastAPI, async SQLAlchemy, Postgres and Redis.

This project demonstrates three ideas:

1. **One thread per participant pair.** The pair is hashed in an order-independent way and the hash has a unique constraint.
2. **Race-safe creation.** The code inserts optimistically; on `IntegrityError` it rolls back and selects the row the other request created.
3. **Unread counters in Redis.** Each send runs `INCR`, and each read resets the counter.

It feeds into **Project 6 (Task Relay, SQS FIFO + Lambda)** and becomes "Conversation create" in the SMS Protocol Replication Service.

> **Status:** a learning project with known design issues. See [Known limitations](#known-limitations) and the
> `REVIEW[HIGH|MED|LOW]` comments in the code before reusing any pattern from it.

## Layout

```
async-thread/
├── app/
│   ├── main.py        # app setup: lifespan, router, /health
│   ├── config.py      # environment variables
│   ├── database.py    # engine, get_db, Redis client
│   ├── models.py      # Thread, ThreadMessage
│   ├── schemas.py     # request/response models
│   ├── routes.py      # HTTP routes
│   └── services.py    # domain logic (participant_hash; more to move here)
├── tests/
│   ├── conftest.py
│   ├── test_threads.py
│   └── test_messages.py
├── pyproject.toml     # Python version + pytest config
└── requirements.txt
```

## Running it

Requires Python 3.11+, plus Postgres 15 and Redis 7 for the server. The tests use SQLite and fakeredis.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

pytest                             # run from the project root

export DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname
export REDIS_URL=redis://:redispassword@localhost:6379
uvicorn app.main:app --reload --port 8005
```

On Windows PowerShell, set the variables with `$env:DATABASE_URL = "..."`.
The tables are created at startup with `create_all`. There are no migrations yet.

## API

Identity is a `sender_id` query parameter for now. The plan calls for JWT (see Known limitations).

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/threads?sender_id=` | Body `{with_user_id}`. Finds or creates the pair's thread and returns `{id, created}`. Always **201**. |
| `POST` | `/threads/{id}/messages?sender_id=` | Body `{body}`. Saves the message, runs `INCR unread:{id}`, returns the message (**201**), or **404** if the thread doesn't exist. |
| `GET` | `/threads/{id}/messages?page=&page_size=` | Returns non-deleted messages, oldest first, with `{messages, total, page}`. **Also resets `unread:{id}` to 0.** |
| `GET` | `/health` | Always returns `{"status": "healthy"}`; it checks no dependencies. |

```bash
curl -X POST "localhost:8005/threads?sender_id=user-a" -H "Content-Type: application/json" -d '{"with_user_id":"user-b"}'
curl -X POST "localhost:8005/threads?sender_id=user-b" -H "Content-Type: application/json" -d '{"with_user_id":"user-a"}'
# same id, created: false
```

## Design notes

**Unique constraint instead of a lock.** Two concurrent `POST /threads` calls for the same pair both try to insert. Postgres's unique index lets one through. The other gets `IntegrityError`, rolls back, and selects the winner's row. This guarantees uniqueness, not mutual exclusion, so don't reuse it to protect work that isn't a single unique row.

**Participant hash.** `sha256('|'.join(sorted([a, b])))` is order-independent but can't be reversed, so the system can't tell who is in a thread. Storing both IDs (`user_low`, `user_high`) with a composite unique constraint gives the same guarantee and keeps membership queryable. That's the recommended direction.

**Unread counts.** Redis `INCR`/`SET` are O(1), but Redis is currently the only record of unread state and isn't updated in the same transaction as the database. The recommended direction is a read pointer per participant in Postgres, with Redis only as a cache.

**ORM only.** Following the project series' rule, all DB access goes through SQLAlchemy ORM/Core constructs, with no `text()` or raw SQL strings.

## Known limitations

These are the most important ones; the code comments have details and fixes.

- **No auth or membership checks.** Any caller can act as any user and read any thread. Fixing this requires storing participants (`app/models.py`).
- **Unread counts are per thread, not per user.** A sender's own messages count as unread, and either participant reading resets the count for both.
- **Lost unread updates.** A message sent between a read's query and its reset is marked read without being shown.
- **No idempotency on send.** A client retry creates a duplicate message, and the Redis write after commit can cause such retries.
- **Ordering relies on app-server clocks**; pagination is offset-based.
- **No migrations** (`create_all` at startup); the plan specifies Alembic.
- **Tests run on SQLite** and run requests one after another, so the concurrency claim is not actually tested.
- `participant_hash` collides for IDs containing `|`. For example, `("a|b","c")` and `("a","b|c")` produce the same hash.

## Next

Fix the `REVIEW[HIGH]` items in this order: `app/models.py` (participants, sequence, idempotency key, status, via Alembic), then the unread-count design, then auth. After that, wire `send_message` into Task Relay through an outbox: SQS gets `{message_id}` only, with `MessageGroupId = thread_id` and `MessageDeduplicationId = message.id`.
