"""
Async Thread project models.

Proves: Participant hash + IntegrityError retry under load + Redis unread counts
"""
# REVIEW: This file is the root cause of most of the HIGH issues in app/routes.py. Make schema changes through
# Alembic (plan Stage 5.1), not create_all.
import hashlib  # REVIEW[LOW]: unused import.
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, ForeignKey, func  # REVIEW[LOW]: `Integer` is unused (until `sequence` is added).
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import DeclarativeBase

# REVIEW[LOW]: unused TYPE_CHECKING block; remove it.
if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# REVIEW[MED]: The plan's Stage 0 puts Base and TimestampMixin in shared/database.py so all six projects
# share them. Import from there instead of defining them again here.
class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# REVIEW[HIGH]: `default=datetime.utcnow` overrides `server_default`, so timestamps come from each app
# server's clock: instances with different clocks reorder messages. utcnow() is also deprecated and has
# no timezone. Keeping only server_default=func.now() is not enough either: Postgres now() is the
# TRANSACTION start time, so rows inserted in one transaction get identical timestamps.
# Fix: `mapped_column(DateTime(timezone=True), server_default=func.now())` for display only, and order
# messages by a per-thread `sequence` (see ThreadMessage).
class TimestampMixin:
    """Mixin that adds created_at timestamp."""
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        default=datetime.utcnow
    )


# REVIEW[HIGH]: Stores only a one-way hash, so the system can never answer "is X in this thread?",
# "who is the recipient?" or "which threads is X in?". That blocks auth (404 for non-participants),
# per-user unread counts and an inbox endpoint.
# Fix: store both participants, sorted, with a composite unique constraint (the same exactly-once
# guarantee, but readable; the hash becomes optional):
#   user_low:  Mapped[str] = mapped_column(String(36), index=True)
#   user_high: Mapped[str] = mapped_column(String(36), index=True)
#   __table_args__ = (UniqueConstraint("user_low", "user_high"),
#                     CheckConstraint("user_low < user_high"))   # also rejects threads with yourself
# Plus per-participant read state for unread counts that can be rebuilt (Redis becomes only a cache):
#   last_read_seq_low / last_read_seq_high: Mapped[int] = mapped_column(default=0)
#   last_sequence: Mapped[int] = mapped_column(default=0)        # incremented on send (see ThreadMessage)
class Thread(Base, TimestampMixin):
    """
    Thread model represents a conversation between two participants.
    
    participant_hash: SHA-256 of '|'.join(sorted([id_a, id_b]))
    Deterministic: (A,B) and (B,A) always produce the same hash
    Unique constraint prevents duplicate conversations
    """
    __tablename__ = "threads"

    # REVIEW[LOW]: `__import__('uuid')` inside a lambda; use `import uuid` at the top and
    # `default=lambda: str(uuid.uuid4())`, or SQLAlchemy's `Uuid` type. Same on ThreadMessage.id.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(__import__('uuid').uuid4()))
    participant_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # REVIEW[MED]: Never used, and lazy-loading it under AsyncSession raises MissingGreenlet.
    # `delete-orphan` also makes hard-deleting a thread wipe its message history.
    # Fix: remove it, or set `lazy="raise"` so accidental lazy loads fail loudly in tests.
    messages: Mapped[list["ThreadMessage"]] = relationship(
        "ThreadMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ThreadMessage.created_at"
    )


# REVIEW[HIGH]: Missing the fields the delivery pipeline (Task Relay, then the SMS service) needs:
#   sequence:          Mapped[int]           # per-thread order: FIFO order, pagination cursor, gap detection.
#                                            # Assign it in the send transaction with
#                                            # update(Thread).where(id==...).values(last_sequence=Thread.last_sequence + 1)
#                                            #     .returning(Thread.last_sequence)
#   client_message_id: Mapped[str]          # idempotency key from the client
#   status:            Mapped[MessageStatus] # PENDING -> SENT -> DELIVERED | FAILED (Secret Handoff's state machine)
#   __table_args__ = (UniqueConstraint("thread_id", "client_message_id"),
#                     UniqueConstraint("thread_id", "sequence"),
#                     Index("ix_msg_thread_seq", "thread_id", "sequence"))
# The docs claim messages are "append-only, never UPDATE", but is_deleted_by_sender and status both
# require UPDATEs. Correct the claim rather than the model.
class ThreadMessage(Base, TimestampMixin):
    """
    ThreadMessage model represents a single message in a thread.
    
    Messages can be soft-deleted by the sender via is_deleted_by_sender flag.
    """
    __tablename__ = "thread_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(__import__('uuid').uuid4()))
    # REVIEW[LOW]: This single-column index becomes redundant once (thread_id, sequence) exists.
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id"), index=True)
    # REVIEW[MED]: String(36) assumes UUID user IDs. If participants become phone numbers (Stage 0 installs
    # `phonenumbers`), decide on E.164 now and size the column to match.
    sender_id: Mapped[str] = mapped_column(String(36), index=True)
    # REVIEW[MED]: The API doesn't enforce this length (SendMessageRequest.body has no max_length), so an
    # oversized body causes a DB error (500) on Postgres; SQLite ignores the limit, so tests never catch it.
    body: Mapped[str] = mapped_column(String(2000))
    # REVIEW[LOW]: Add `server_default=false()` so rows inserted outside the ORM also get False.
    is_deleted_by_sender: Mapped[bool] = mapped_column(Boolean, default=False)

    thread: Mapped["Thread"] = relationship("Thread", back_populates="messages")
