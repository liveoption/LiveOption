"""Settings for the LiveOption Web Job Server.

All settings are loaded from environment variables prefixed with
``LIVEOPTION_`` (e.g. ``LIVEOPTION_INTERNAL_SECRET``).  A ``.env`` file
at the working directory is also honored.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LIVEOPTION_",
        env_file=".env",
        extra="ignore",
    )

    internal_secret: SecretStr = Field(
        ...,
        description="Shared secret required on /_ingest/events. Env: LIVEOPTION_INTERNAL_SECRET",
    )
    db_path: Path = Field(
        Path.home() / "LiveOption" / "web" / "liveoption.db",
        description="SQLite database path. Env: LIVEOPTION_DB_PATH",
    )
    runs_dir: Path = Field(
        Path.home() / "LiveOption" / "runs",
        description="Runs directory root. Env: LIVEOPTION_RUNS_DIR",
    )
    model_settings_path: Path | None = Field(
        None, description="Server-side model group registry; defaults beside the database"
    )
    host: str = Field("127.0.0.1", description="Env: LIVEOPTION_HOST")
    port: int = Field(19720, description="Env: LIVEOPTION_PORT")
    ingest_url: str = Field(
        "http://127.0.0.1:19720/_ingest/events",
        description="Env: LIVEOPTION_INGEST_URL",
    )
    # Shared cap across forks + batch launches. Used by supervisor.spawn_fork
    # and supervisor.spawn_batch.
    max_concurrent_runs: int = 4
    max_streaming_questions_per_checkpoint: int = 3
    rate_limit_llm_per_min: int = 10


def get_settings() -> Settings:
    return Settings()
