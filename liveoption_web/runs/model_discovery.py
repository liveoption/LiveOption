"""Credential-scoped provider model discovery; never forward upstream errors or links."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlsplit

import requests

from liveoption_web.runs.model_settings import ModelEntry, ModelGroup, ModelSettings


class DiscoveryError(ValueError):
    pass


def endpoint(group: ModelGroup) -> str:
    defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com",
                "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
                "deepseek": "https://api.deepseek.com", "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}
    base = (group.base_url or defaults.get(group.provider, "")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DiscoveryError("invalid_models_base_url")
    return base + ("/v1/models" if group.provider == "anthropic" and not parsed.path.endswith("/v1") else "/models")


def discover(group: ModelGroup) -> list[dict[str, str]]:
    if not group.api_key:
        raise DiscoveryError("api_key_required")
    url = endpoint(group)
    headers = {"Authorization": f"Bearer {group.api_key}", "Accept": "application/json"}
    if group.provider == "anthropic":
        headers.update({"x-api-key": group.api_key, "anthropic-version": "2023-06-01"})
    params: dict = {}
    found: dict[str, dict[str, str]] = {}
    cursors = set()
    for _ in range(100):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=(5, 20), allow_redirects=False)
            if not response.ok or response.is_redirect:
                raise DiscoveryError(f"models_endpoint_http_{response.status_code}")
            value = response.json()
        except requests.RequestException:
            raise DiscoveryError("models_endpoint_unreachable") from None
        except ValueError as exc:
            if isinstance(exc, DiscoveryError):
                raise
            raise DiscoveryError("invalid_models_response") from None
        items = value.get("data", value.get("models")) if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise DiscoveryError("invalid_models_response")
        for item in items:
            if not isinstance(item, dict):
                raise DiscoveryError("invalid_model_entry")
            name = item.get("id") or item.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 512 or any(ord(c) < 32 for c in name):
                raise DiscoveryError("invalid_model_identifier")
            label = item.get("display_name") or item.get("displayName") or name
            if group.api_key in name or group.api_key in str(label):
                raise DiscoveryError("invalid_models_response")
            found[name] = {"api_model": name, "label": str(label)[:100]}
        if not isinstance(value, dict):
            return list(found.values())
        cursor = value.get("next_page_token") or value.get("nextPageToken")
        cursor_key = "page_token" if value.get("next_page_token") else "pageToken"
        if value.get("has_more"):
            cursor = value.get("last_id") or (items[-1].get("id") if items else None)
            cursor_key = "after_id" if group.provider == "anthropic" else "after"
        if not cursor:
            if value.get("has_more"):
                raise DiscoveryError("invalid_models_pagination")
            return list(found.values())
        if not isinstance(cursor, str) or cursor in cursors:
            raise DiscoveryError("invalid_models_pagination")
        cursors.add(cursor)
        params = {cursor_key: cursor}
    raise DiscoveryError("models_pagination_limit_exceeded")


def merge_discovered(settings: ModelSettings, group_id: str, entries: list[dict[str, str]]) -> ModelSettings:
    """Keep stable existing IDs / display names, mark disappeared models unavailable."""
    result = settings.model_copy(deep=True)
    found = {item["api_model"]: item for item in entries}
    existing = {model.api_model: model for model in result.models if model.group_id == group_id}
    for model in result.models:
        if model.group_id == group_id:
            model.available = model.api_model in found
    for name, item in found.items():
        if name in existing:
            continue
        identifier = "model-" + sha256((group_id + "\0" + name).encode()).hexdigest()[:24]
        result.models.append(ModelEntry(id=identifier, group_id=group_id, label=item["label"], api_model=name))
    group = next(group for group in result.groups if group.id == group_id)
    group.models_updated_at = datetime.now(timezone.utc).isoformat()
    result.validate_references()
    return result
