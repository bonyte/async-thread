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
├── scripts/
│   ├── check.py       # tests + real-stack smoke test (run before every commit)
│   └── smoke.py       # end-to-end checks against a running server
├── docker-compose.yml # Postgres 15 + Redis 7 for local dev
├── pyproject.toml     # Python version + pytest config
└── requirements.txt
```

## Running it

Requires Python 3.12 (see `.python-version`) and Docker. The unit tests use SQLite and fakeredis; the server and the smoke test use Postgres 15 and Redis 7 from `docker-compose.yml`. Those are published on host ports **55432/56379**, because 5432, 5433 and 6379 are taken on the dev machine.

```bash
py -3.12 -m venv .venv             # macOS/Linux: python3.12 -m venv .venv
source .venv/Scripts/activate      # PowerShell: .venv\Scripts\Activate.ps1 · macOS/Linux: .venv/bin/activate
pip install -r requirements.txt

docker compose up -d --wait        # Postgres + Redis
python scripts/check.py            # tests + start server + smoke test, then stop

# Run the server yourself:
export DATABASE_URL=postgresql+asyncpg://user:password@localhost:55432/dbname
export REDIS_URL=redis://:redispassword@localhost:56379
uvicorn app.main:app --reload --port 8005
```

On Windows PowerShell, set the variables with `$env:DATABASE_URL = "..."`.
The tables are created at startup with `create_all`. There are no migrations yet.

## Development workflow

### Test every change
| When | Run | Time |
|---|---|---|
| After each edit | `pytest -x -q` (stop at the first failure) or `pytest --lf` (rerun only what failed) | seconds |
| Before each commit | `python scripts/check.py`: tests, then a live server on real Postgres + Redis, then `scripts/smoke.py` | ~15 s |
| End of each step | Full check, then commit, then tag a checkpoint (below) | |

Rules:
- Never commit while the check is red.
- When behaviour changes on purpose, update the test **in the same commit**, and update `scripts/smoke.py` when the API changes.
- Make one logical change per commit, so any single change can be undone on its own.

### Checkpoints and restoring earlier versions
The rework happens on the `rework` branch. `main` and the tag `baseline-original` keep the original project.

```bash
git tag -a day1-done -m "Day 1: Alembic, settings, Postgres tests"   # mark a known-good state
git tag -n                                                     # list checkpoints
git log --oneline --decorate                                   # history with tags

git diff                          # what changed since the last commit
git restore app/routes.py         # discard uncommitted edits to one file
git stash push -m "experiment"    # park uncommitted work; bring it back with `git stash pop`

git revert <commit>               # undo one commit safely (adds an inverse commit)
git switch --detach day1-done     # inspect/run an old checkpoint; return with `git switch rework`
git switch -c retry day1-done     # start over from a checkpoint on a new branch, keeping the old attempt
git reset --hard day1-done        # DESTRUCTIVE: drop all commits and edits after the checkpoint
```

To restore the **database**, use `docker compose down -v && docker compose up -d --wait`. This deletes the local data volume, which is fine here because all data is test data. Once Alembic is in, use `alembic downgrade -1` to step back one migration.

To restore **dependencies**, delete `.venv` and reinstall from `requirements.txt`. Checking out an old checkpoint also brings back its `requirements.txt`.

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
