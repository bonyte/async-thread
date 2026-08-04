"""
Async Thread project models.

Proves: Participant hash + IntegrityError retry under load + Redis unread counts
"""
import hashlib
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


class TimestampMixin:
    """Mixin that adds created_at timestamp."""
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        default=datetime.utcnow
    )


class Thread(Base, TimestampMixin):
    """
    Thread model represents a conversation between two participants.
    
    participant_hash: SHA-256 of '|'.join(sorted([id_a, id_b]))
    Deterministic: (A,B) and (B,A) always produce the same hash
    Unique constraint prevents duplicate conversations
    """
    __tablename__ = "threads"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(__import__('uuid').uuid4()))
    participant_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    
    messages: Mapped[list["ThreadMessage"]] = relationship(
        "ThreadMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ThreadMessage.created_at"
    )


class ThreadMessage(Base, TimestampMixin):
    """
    ThreadMessage model represents a single message in a thread.
    
    Messages can be soft-deleted by the sender via is_deleted_by_sender flag.
    """
    __tablename__ = "thread_messages"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(__import__('uuid').uuid4()))
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id"), index=True)
    sender_id: Mapped[str] = mapped_column(String(36), index=True)
    body: Mapped[str] = mapped_column(String(2000))
    is_deleted_by_sender: Mapped[bool] = mapped_column(Boolean, default=False)
    
    thread: Mapped["Thread"] = relationship("Thread", back_populates="messages")
