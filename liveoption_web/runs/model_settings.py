"""Server-side model groups. Credentials never appear in API responses or run specs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class ModelGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1, max_length=100)
    provider: Literal["anthropic", "openai_compatible", "openai", "gemini", "deepseek", "qwen"]
    base_url: str = ""
    api_key: str = ""
    models_updated_at: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("group id must be 1–64 letters, numbers, dots, dashes or underscores")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        return value.rstrip("/")


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    group_id: str
    label: str = Field(min_length=1, max_length=100)
    api_model: str = Field(min_length=1, max_length=512)
    available: bool = True

    @field_validator("id", "group_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("id must be 1–64 letters, numbers, dots, dashes or underscores")
        return value


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[ModelGroup]
    models: list[ModelEntry]

    def validate_references(self) -> None:
        group_ids = [group.id for group in self.groups]
        model_ids = [model.id for model in self.models]
        if len(group_ids) != len(set(group_ids)) or len(model_ids) != len(set(model_ids)):
            raise ValueError("duplicate group or model id")
        if any(model.group_id not in group_ids for model in self.models):
            raise ValueError("model references an unknown group")

    def public(self) -> dict:
        return {
            "groups": [
                {**group.model_dump(exclude={"api_key"}), "has_api_key": bool(group.api_key)}
                for group in self.groups
            ],
            "models": [model.model_dump() for model in self.models],
        }


def settings_path(settings) -> Path:
    return settings.model_settings_path or settings.db_path.parent / "model-settings.json"


def default_settings() -> ModelSettings:
    return ModelSettings(
        groups=[
            ModelGroup(id="anthropic-gateway", label="Anthropic gateway", provider="anthropic",
                       base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
                       api_key=os.getenv("ANTHROPIC_AUTH_TOKEN", "") or os.getenv("ANTHROPIC_API_KEY", "")),
            ModelGroup(id="openai-gateway", label="OpenAI gateway", provider="openai_compatible",
                       base_url=os.getenv("OPENAI_BASE_URL", ""), api_key=os.getenv("OPENAI_API_KEY", "")),
        ],
        models=[
            ModelEntry(id="gemini-3-flash", group_id="anthropic-gateway", label="Gemini 3 Flash", api_model="gemini-3-flash"),
            ModelEntry(id="gpt-5.6-terra", group_id="openai-gateway", label="GPT-5.6 Terra", api_model="gpt-5.6-terra"),
        ],
    )


def load_model_settings(settings) -> ModelSettings:
    path = settings_path(settings)
    if not path.exists():
        return default_settings()
    value = ModelSettings.model_validate_json(path.read_text(encoding="utf-8"))
    value.validate_references()
    return value


def save_model_settings(settings, value: ModelSettings) -> None:
    value.validate_references()
    path = settings_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value.model_dump(), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def merge_public_update(settings, payload: ModelSettings) -> ModelSettings:
    """Blank key means retain the existing credential for that group id."""
    old = {group.id: group for group in load_model_settings(settings).groups}
    for group in payload.groups:
        if not group.api_key and group.id in old:
            group.api_key = old[group.id].api_key
        previous = old.get(group.id)
        group.models_updated_at = previous.models_updated_at if previous and all(
            getattr(previous, key) == getattr(group, key) for key in ("provider", "base_url", "api_key")
        ) else None
    payload.validate_references()
    return payload
