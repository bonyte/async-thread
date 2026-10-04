"""Configuration read from environment variables."""
import os

# REVIEW[MED]: Credentials are hardcoded as fallback defaults, so a missing env var silently connects with them.
# Fix: use pydantic-settings (already in requirements.txt) and fail at startup when a value is missing:
#   class Settings(BaseSettings):
#       database_url: str
#       redis_url: str
#   settings = Settings()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/dbname"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://:redispassword@localhost:6379")
