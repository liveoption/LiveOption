"""LLM client — unified 2-dispatch wrapper with call logging.

Supports: deepseek, gemini, openai, openai_compatible, qwen, claude, anthropic, mock.
All OpenAI-compatible providers share one method. Claude uses Anthropic SDK.
Every call is optionally saved as a JSON file via LLMCallLogger.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.config import ModelsConfig
    from trading.output.llm_call_logger import LLMCallLogger

logger = logging.getLogger(__name__)


def _parse_retry_delay(exc: Exception) -> float | None:
    """Extract retry delay seconds from a Gemini/Google API RateLimitError.

    The error body contains: details[].retryDelay = "54s" or "54.36s".
    Returns None if not parseable.
    """
    try:
        body = exc.response.json()  # type: ignore[union-attr]
        for detail in body.get("error", {}).get("details", []):
            rd = detail.get("retryDelay", "")
            if rd:
                return float(rd.rstrip("s"))
    except Exception:
        pass
    return None


_PROVIDER_MAP = {
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "openai": (None, "OPENAI_API_KEY"),
    "openai_compatible": (None, "OPENAI_API_KEY"),
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "claude": (None, "ANTHROPIC_API_KEY"),
    "anthropic": (None, "ANTHROPIC_API_KEY"),
    "mock": (None, None),
}


class LLMClient:
    def __init__(self, provider: str = "deepseek", model: str = "deepseek-chat",
                 api_key: str = "", base_url: str = "",
                 temperature: float = 0.3, max_tokens: int = 4096,
                 log_dir: Path | str | None = None,
                 max_retries: int = 6, retry_base_delay: float = 12.0,
                 reasoning_effort: str | None = None,
                 models_config: "ModelsConfig | None" = None,
                 default_model_id: str | None = None) -> None:
        self.provider = provider
        self.model = model
        self._temperature = temperature
        self._max_tokens = max_tokens if provider not in ("claude", "anthropic") else min(max_tokens, 2048)
        self._total_tokens = 0
        self._call_logger: LLMCallLogger | None = None
        self._mock_call_count = 0
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._reasoning_effort = reasoning_effort

        info = _PROVIDER_MAP.get(provider)
        if info is None:
            raise ValueError(f"Unknown provider: {provider!r}")
        default_base_url, env_key = info
        self._api_key = api_key or (os.environ.get(env_key, "") if env_key else "")
        if not self._api_key and provider in ("claude", "anthropic"):
            self._api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self._base_url = base_url or os.environ.get("LIVEOPTION_LLM_BASE_URL", "") or default_base_url or ""
        if not self._base_url and provider in ("openai", "openai_compatible"):
            self._base_url = os.environ.get("OPENAI_BASE_URL", "")
        if not self._base_url and provider in ("claude", "anthropic"):
            self._base_url = os.environ.get("ANTHROPIC_BASE_URL", "")

        try:
            self._min_request_interval = max(
                0.0, float(os.environ.get("LIVEOPTION_LLM_MIN_INTERVAL_SECONDS", "0"))
            )
        except ValueError:
            self._min_request_interval = 0.0
        self._last_request_started_at = 0.0

        if log_dir is not None:
            from trading.output.llm_call_logger import LLMCallLogger as _Logger
            self._call_logger = _Logger(Path(log_dir))

        # Optional runtime model-override support (used by the web fork/diverge flow).
        # When `models_config` is provided, callers can swap the effective model at
        # runtime via `set_model_override`. Without `models_config`, the override
        # method is unavailable (raises RuntimeError) and this client behaves
        # identically to the legacy single-provider/model construction.
        self._models_config: "ModelsConfig | None" = models_config
        self._default_model_id: str | None = default_model_id
        self._model_override: str | None = None

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def model_name(self) -> str:
        return self.model

    def set_call_logger(self, call_logger: LLMCallLogger) -> None:
        self._call_logger = call_logger

    def set_model_override(self, model_id: str | None) -> None:
        """Override the model_id used for subsequent `call()`s.

        The override must be a key present in the injected `ModelsConfig.models`
        registry (typically loaded from `configs/models.yaml`). Pass `None` to
        clear the override and revert to the default model/provider supplied at
        construction time.

        Raises:
            RuntimeError: if this client was not constructed with a
                `models_config` (runtime overrides are disabled).
            ValueError: if `model_id` is unknown in the registry.
        """
        if self._models_config is None:
            raise RuntimeError(
                "set_model_override requires LLMClient to be constructed with a "
                "models_config; none was provided."
            )
        if model_id is not None and model_id not in self._models_config.models:
            raise ValueError(f"unknown model: {model_id}")
        self._model_override = model_id

    def _apply_override_fields(self) -> tuple[str, str, str, str, int, str | None]:
        """Return (provider, model, api_key, base_url, max_tokens, reasoning_effort)
        for the currently-active model.

        When an override is set, the fields come from the ModelsConfig entry.
        Otherwise the client's constructor-supplied fields are used.
        """
        if self._model_override and self._models_config is not None:
            entry = self._models_config.get_entry(self._model_override)
            provider = entry.provider
            info = _PROVIDER_MAP.get(provider)
            default_base_url = info[0] if info else None
            env_key = info[1] if info else None
            api_key = entry.resolved_api_key() or (
                os.environ.get(env_key, "") if env_key else ""
            )
            base_url = entry.resolved_base_url() or default_base_url or ""
            max_tokens = entry.max_tokens
            if provider in ("claude", "anthropic"):
                max_tokens = min(max_tokens, 2048)
            return (
                provider,
                entry.model,
                api_key,
                base_url,
                max_tokens,
                entry.reasoning_effort,
            )
        return (
            self.provider,
            self.model,
            self._api_key,
            self._base_url,
            self._max_tokens,
            self._reasoning_effort,
        )

    def decide(self, system_prompt: str, user_prompt: str) -> list[dict]:
        from trading.prompts.parsers import parse_json_array_response
        self._last_raw_response = self._call_api(system_prompt, user_prompt)
        return parse_json_array_response(self._last_raw_response)

    def call_raw(self, system_prompt: str, user_prompt: str) -> str:
        return self._call_api(system_prompt, user_prompt)

    def _mock_response(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a plausible mock response so executor calls are not skipped."""
        import json, re
        self._mock_call_count += 1
        sp_lower = system_prompt.lower()

        # ── Executor: return a minimal buy order using the first contract listed ──
        # Match only the executor system prompt opening, not analyzer prompts that
        # mention "executor" in passing (e.g. "the executor handles that").
        is_executor = sp_lower.startswith("you are a trade execution planner")
        if is_executor:
            # Find the first OPRA contract in the user prompt (prefer put for bearish intent)
            puts  = re.findall(r'O:\S+P\d{8}', user_prompt)
            calls = re.findall(r'O:\S+C\d{8}', user_prompt)
            contracts = puts + calls
            if contracts:
                contract = contracts[0]
                # Try to extract price after "px=$"
                m = re.search(rf'{re.escape(contract)}.*?px=\$(\d+\.\d+)', user_prompt)
                price = round(float(m.group(1)), 2) if m else 1.00
                order = [{
                    "action": "buy",
                    "contract": contract,
                    "qty": 1,
                    "order_type": "LIMIT",
                    "limit_price": price,
                    "spread_group": None,
                    "rationale": "mock order for prompt-dump testing",
                }]
                return json.dumps(order)
            return "[]"

        # ── Analyzer: alternate bullish/bearish each call ──
        intent = "bearish" if self._mock_call_count % 2 == 0 else "bullish"

        # Earnings / overlay analyzers expect an intent + spread fields
        if "earnings" in sp_lower or "spread" in sp_lower or "overlay" in sp_lower:
            resp = {
                "intent": intent,
                "confidence": 0.70,
                "reasoning": f"Mock {intent} signal — prompt-dump dry run.",
                "target_instruments": [],
                "spread_type": "put_debit_spread" if intent == "bearish" else "call_debit_spread",
            }
        else:
            resp = {
                "intent": intent,
                "confidence": 0.70,
                "reasoning": f"Mock {intent} signal — prompt-dump dry run.",
                "target_instruments": [],
            }
        return json.dumps(resp)

    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        # Resolve the effective provider/model/etc. for this call. When a
        # model_override is active these differ from the constructor fields;
        # we swap them onto `self` for the duration of the call so the existing
        # provider helpers (_call_openai_compat / _call_anthropic) pick them up
        # without further refactoring.
        eff_provider, eff_model, eff_key, eff_base, eff_max, eff_reason = (
            self._apply_override_fields()
        )
        saved = (
            self.provider, self.model, self._api_key, self._base_url,
            self._max_tokens, self._reasoning_effort,
        )
        self.provider, self.model = eff_provider, eff_model
        self._api_key, self._base_url = eff_key, eff_base
        self._max_tokens, self._reasoning_effort = eff_max, eff_reason
        try:
            return self._call_api_inner(system_prompt, user_prompt)
        finally:
            (
                self.provider, self.model, self._api_key, self._base_url,
                self._max_tokens, self._reasoning_effort,
            ) = saved

    def _call_api_inner(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "mock":
            response = self._mock_response(system_prompt, user_prompt)
            if self._call_logger is not None:
                self._call_logger.log_call(
                    system_prompt=system_prompt, user_prompt=user_prompt,
                    response=response, model=self.model or "mock", provider="mock",
                )
            return response
        sp_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]
        logger.debug("LLM call: provider=%s model=%s sys_hash=%s user_len=%d",
                      self.provider, self.model, sp_hash, len(user_prompt))
        self._wait_for_request_slot()
        start = time.monotonic()
        if self.provider in ("claude", "anthropic"):
            response, token_info = self._call_anthropic(system_prompt, user_prompt)
        else:
            response, token_info = self._call_openai_compat(system_prompt, user_prompt)
        elapsed_ms = (time.monotonic() - start) * 1000
        total = token_info.get("total", 0)
        self._total_tokens += total
        logger.debug("LLM response: len=%d tokens=%d latency=%.0fms", len(response), total, elapsed_ms)
        if self._call_logger is not None:
            self._call_logger.log_call(
                system_prompt=system_prompt, user_prompt=user_prompt,
                response=response, model=self.model, provider=self.provider,
                input_tokens=token_info.get("input"),
                output_tokens=token_info.get("output"),
                total_tokens=total, latency_ms=elapsed_ms,
            )
        return response

    def _wait_for_request_slot(self) -> None:
        """Throttle request starts locally or across sibling worker processes."""
        if self._min_request_interval <= 0:
            return

        rate_limit_dir = os.environ.get("LIVEOPTION_LLM_RATE_LIMIT_DIR", "")
        if rate_limit_dir:
            import fcntl

            directory = Path(rate_limit_dir)
            directory.mkdir(parents=True, exist_ok=True)
            model_key = hashlib.sha256(self.model.encode()).hexdigest()[:16]
            lock_path = directory / f"{model_key}.lock"
            with lock_path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    raw = handle.read().strip()
                    last_started = float(raw) if raw else 0.0
                    wait = self._min_request_interval - (time.time() - last_started)
                    if wait > 0:
                        time.sleep(wait)
                    started = time.time()
                    handle.seek(0)
                    handle.truncate()
                    handle.write(f"{started:.6f}\n")
                    handle.flush()
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return

        if self._last_request_started_at > 0:
            elapsed = time.monotonic() - self._last_request_started_at
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
        self._last_request_started_at = time.monotonic()

    def _call_openai_compat(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        import random
        from openai import OpenAI, RateLimitError, APIStatusError
        kwargs: dict = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        # Disable SDK-level retries for Gemini — its rate-limit window requires
        # longer delays (12 s+) that only our custom backoff provides.
        if self.provider == "gemini":
            kwargs["max_retries"] = 0
        client = OpenAI(**kwargs)
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}]
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                extra: dict = {}
                if self.provider == "gemini" and self._reasoning_effort:
                    extra["reasoning_effort"] = self._reasoning_effort
                resp = client.chat.completions.create(
                    model=self.model, messages=messages,
                    temperature=self._temperature, max_tokens=self._max_tokens,
                    **extra,
                )
                usage = resp.usage
                ti = {"input": usage.prompt_tokens, "output": usage.completion_tokens,
                      "total": usage.total_tokens} if usage else {}
                return resp.choices[0].message.content or "", ti
            except RateLimitError as e:
                last_exc = e
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                    # Honour the API's suggested retry delay when present (e.g. Gemini retryDelay)
                    suggested = _parse_retry_delay(e)
                    if suggested is not None:
                        delay = max(delay, suggested + random.uniform(1, 3))
                    logger.warning("LLM 429 rate-limit (attempt %d/%d), retrying in %.1fs: %s",
                                   attempt + 1, self._max_retries, delay, e)
                    time.sleep(delay)
            except APIStatusError as e:
                # Retry on transient server errors (5xx); don't retry on 4xx auth failures
                if e.status_code >= 500 and attempt < self._max_retries:
                    last_exc = e
                    delay = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("LLM %d server error (attempt %d/%d), retrying in %.1fs: %s",
                                   e.status_code, attempt + 1, self._max_retries, delay, e)
                    time.sleep(delay)
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        import random
        import anthropic
        kwargs: dict = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = anthropic.Anthropic(**kwargs)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                msg = client.messages.create(
                    model=self.model, max_tokens=self._max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                ti = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens,
                      "total": msg.usage.input_tokens + msg.usage.output_tokens}
                return msg.content[0].text, ti
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Anthropic-compatible 429 (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, self._max_retries, delay, exc,
                    )
                    time.sleep(delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt < self._max_retries:
                    last_exc = exc
                    delay = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Anthropic-compatible %d (attempt %d/%d), retrying in %.1fs: %s",
                        exc.status_code, attempt + 1, self._max_retries, delay, exc,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc  # type: ignore[misc]
