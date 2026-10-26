"""PromptLoader — load prompt templates from text files with variable substitution.

Prompts live in the prompts/ directory at the project root:
  prompts/system/{task_type}.txt   — system prompts
  prompts/user/{task_type}.txt     — user prompt templates

Templates use {VARIABLE_NAME} placeholders. Literal JSON braces are preserved.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class PromptLoader:
    """Load and render prompt templates from text files."""

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        self._base = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR

    def load_system(self, task_type: str, override_path: str | None = None) -> str:
        """Load a system prompt template.

        Args:
            task_type: e.g. "weekly", "earnings_pre", "overlay_profit"
            override_path: If set, load from this path instead of default.
        """
        if override_path:
            return Path(override_path).read_text()
        path = self._base / "system" / f"{task_type}.txt"
        if not path.exists():
            logger.warning("System prompt not found: %s", path)
            return ""
        return path.read_text()

    def load_user(self, task_type: str, override_path: str | None = None) -> str:
        """Load a user prompt template."""
        if override_path:
            return Path(override_path).read_text()
        path = self._base / "user" / f"{task_type}.txt"
        if not path.exists():
            logger.warning("User prompt not found: %s", path)
            return ""
        return path.read_text()

    @staticmethod
    def render(template: str, **variables) -> str:
        """Substitute named placeholders without interpreting JSON objects."""
        # Prompt text often includes literal JSON objects. Replace only named
        # placeholders so JSON braces cannot disable *all* substitutions.
        marker = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(:[^{}]+)?\}(?!\})")
        from trading.prompts.provenance import RenderedPrompt
        def replace(match: re.Match[str]) -> str:
            value = variables.get(match.group(1), "")
            spec = match.group(2)
            if not spec:
                return str(value)
            try:
                return format(value, spec[1:])
            except (TypeError, ValueError):
                logger.warning("Invalid prompt format for %s", match.group(1))
                return str(value)
        parts = []
        previous = 0
        for match in marker.finditer(template):
            parts.append((template[previous:match.start()], None, ""))
            parts.append((replace(match), match.group(1), (match.group(2) or "").lstrip(":")))
            previous = match.end()
        parts.append((template[previous:], None, ""))
        text = "".join(part[0] for part in parts).replace("{{", "{").replace("}}", "}")
        normalized = [(text.replace("{{", "{").replace("}}", "}"), name, spec) for text, name, spec in parts]
        # Preserve the old formatter's exact behavior even for unusual brace
        # escapes spanning a substitution boundary. Such spans are unlabelled.
        if "".join(part[0] for part in normalized) != text:
            return text
        return RenderedPrompt(text, normalized)


# Module-level singleton for convenience
_default_loader: PromptLoader | None = None


def get_loader(prompts_dir: str | Path | None = None) -> PromptLoader:
    """Get or create a module-level PromptLoader."""
    global _default_loader
    if _default_loader is None or prompts_dir is not None:
        _default_loader = PromptLoader(prompts_dir)
    return _default_loader
