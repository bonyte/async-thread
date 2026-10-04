"""Business logic shared by the routes (and later by Task Relay / the SMS service)."""
import hashlib

# REVIEW[MED]: Only participant_hash lives here so far. As part of the fixes, move the logic out of
# routes.py into find_or_create_thread(), send_message() and mark_read(), so other services can call them
# directly instead of going through HTTP. Routes should only translate HTTP <-> these functions.


# REVIEW[HIGH]: The '|' separator can collide. sorted(["a|b","c"]) and sorted(["a","b|c"]) both join to
# "a|b|c", so two different pairs share ONE thread (a privacy leak). This is safe only if IDs can never
# contain '|', and with_user_id is an unvalidated str.
# Fix: validate the ID format (UUID or normalized E.164) before hashing, or drop the hash and store
# (user_low, user_high) with a composite unique constraint (see models.py).
def participant_hash(id_a: str, id_b: str) -> str:
    """
    Compute deterministic hash of two participant IDs.

    Returns SHA-256 of '|'.join(sorted([id_a, id_b]))
    Order-independent: (A,B) and (B,A) produce same hash.
    """
    sorted_ids = sorted([id_a, id_b])
    combined = "|".join(sorted_ids)
    return hashlib.sha256(combined.encode()).hexdigest()
