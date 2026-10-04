"""
One command to verify the project after every change.

    python scripts/check.py          # tests + real-stack smoke test (Docker Postgres/Redis + live server)
    python scripts/check.py --fast   # tests only, a few seconds; use while iterating

Exits non-zero at the first failing step, so it can gate commits:
    python scripts/check.py && git commit ...
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PORT = 8005
# Matches docker-compose.yml host ports; override by setting the env vars yourself.
STACK_ENV = {
    "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql+asyncpg://user:password@localhost:55432/dbname"),
    "REDIS_URL": os.environ.get("REDIS_URL", "redis://:redispassword@localhost:56379"),
}


def step(title: str, cmd: list[str], env: dict | None = None) -> None:
    print(f"\n=== {title}: {' '.join(cmd)}", flush=True)
    if subprocess.run(cmd, cwd=ROOT, env=env).returncode != 0:
        sys.exit(f"\nFAILED: {title}")


def wait_for_health(timeout_s: float = 20) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://localhost:{PORT}/health", timeout=1).status_code == 200:
                return True
        except httpx.TransportError:
            pass
        time.sleep(0.5)
    return False


def main() -> None:
    step("Tests", [PY, "-m", "pytest", "-q"])
    if "--fast" in sys.argv:
        print("\nOK (fast mode: skipped the real-stack smoke test)")
        return

    step("Postgres + Redis", ["docker", "compose", "up", "-d", "--wait"])

    print(f"\n=== Server on :{PORT} (DATABASE_URL={STACK_ENV['DATABASE_URL']})", flush=True)
    log_path = ROOT / ".check-server.log"
    with open(log_path, "w") as log:
        server = subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
            cwd=ROOT, env={**os.environ, **STACK_ENV}, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            if not wait_for_health():
                sys.exit(f"\nFAILED: server did not become healthy; see {log_path.name}")
            step("Smoke test", [PY, "scripts/smoke.py", f"http://localhost:{PORT}"])
        finally:
            server.terminate()
            server.wait(timeout=10)

    print("\nOK: tests and real-stack smoke test passed")


if __name__ == "__main__":
    main()
