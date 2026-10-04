"""Request and response models for the HTTP API."""
from pydantic import BaseModel


# REVIEW[MED]: `with_user_id: str` has no format rules, so "ABC" and "abc" become two threads, and a thread
# with yourself is accepted. Fix: `with_user_id: UUID` (or a normalized E.164 string if conversations are
# keyed by phone number), plus a check that rejects with_user_id == the caller's ID with 422.
class CreateThreadRequest(BaseModel):
    """Request body for creating a thread."""
    with_user_id: str


# REVIEW[LOW]: Add `participant_ids` once the model stores them (see models.py).
class ThreadResponse(BaseModel):
    """Response body for thread creation."""
    id: str
    created: bool


# REVIEW[HIGH]: No idempotency key and no length validation.
# Fix:
#   client_message_id: UUID                            # unique per (thread_id, client_message_id)
#   body: str = Field(min_length=1, max_length=1600)   # pick N from your SMS segment limit
# Today an empty body is accepted, and an oversized one hits String(2000) on Postgres and returns a 500.
class SendMessageRequest(BaseModel):
    """Request body for sending a message."""
    body: str


# REVIEW[HIGH]: Not ready for the delivery pipeline. Add `status: MessageStatus` (always "pending" until
# Task Relay exists), `sequence: int` (per-thread order, FIFO and the pagination cursor) and `thread_id`.
# `created_at: str` is a timestamp with no timezone; use `AwareDatetime`.
# Add `model_config = ConfigDict(from_attributes=True)` and build responses with
# MessageResponse.model_validate(msg) instead of copying fields by hand twice.
class MessageResponse(BaseModel):
    """Response body for message details."""
    id: str
    sender_id: str
    body: str
    created_at: str


# REVIEW[MED]: `total` costs a COUNT(*) per request, and `page` means offset pagination.
# Fix: `next_cursor: int | None` (the last sequence returned), and drop `total`.
class ThreadMessagesResponse(BaseModel):
    """Response body for reading thread messages."""
    messages: list[MessageResponse]
    total: int
    page: int
