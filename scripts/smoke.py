"""
End-to-end smoke test against a running server backed by real Postgres + Redis.

Usage (server must already be running, e.g. `uvicorn app.main:app --port 8005`):
    python scripts/smoke.py [base_url]

Exits non-zero on the first failed check. Update the checks as the API changes
(auth, /v1 prefix, cursor pagination, ...).
"""
import sys
import uuid

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8005"


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  ({detail})" if not condition else ""))
    if not condition:
        sys.exit(1)


def main() -> None:
    # Fresh IDs per run so repeated runs don't collide with earlier data.
    user_a, user_b = f"smoke-{uuid.uuid4().hex[:8]}", f"smoke-{uuid.uuid4().hex[:8]}"

    with httpx.Client(base_url=BASE_URL, timeout=5) as client:
        r = client.get("/health")
        check("GET /health", r.status_code == 200, r.text)

        r1 = client.post("/threads", params={"sender_id": user_a}, json={"with_user_id": user_b})
        check("POST /threads creates", r1.status_code == 201 and r1.json()["created"] is True, r1.text)
        thread_id = r1.json()["id"]

        r2 = client.post("/threads", params={"sender_id": user_b}, json={"with_user_id": user_a})
        check("POST /threads reversed pair -> same thread",
              r2.json().get("id") == thread_id and r2.json().get("created") is False, r2.text)

        for body in ("first", "second"):
            r = client.post(f"/threads/{thread_id}/messages", params={"sender_id": user_a}, json={"body": body})
            check(f"POST message '{body}'", r.status_code == 201, r.text)

        r = client.get(f"/threads/{thread_id}/messages")
        bodies = [m["body"] for m in r.json().get("messages", [])]
        check("GET messages in order", r.status_code == 200 and bodies == ["first", "second"], r.text)

        r = client.get(f"/threads/{uuid.uuid4()}/messages")
        check("GET unknown thread -> 404", r.status_code == 404, r.status_code)

    print(f"\nAll smoke checks passed against {BASE_URL}")


if __name__ == "__main__":
    main()
