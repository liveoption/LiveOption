"""Track template substitutions without changing the text sent to a model."""
from __future__ import annotations


class RenderedPrompt(str):
    def __new__(cls, text: str, parts=()):
        instance = super().__new__(cls, text)
        instance.parts = tuple(parts)
        return instance

    def __add__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        parts = other.parts if isinstance(other, RenderedPrompt) else ((other, None, ""),)
        return RenderedPrompt(str(self) + str(other), self.parts + parts)

    def __radd__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return RenderedPrompt(str(other) + str(self), ((other, None, ""),) + self.parts)

    def spans(self):
        offset = 0
        result = []
        for text, variable, spec in self.parts:
            if variable is not None:
                result.append({"start": offset, "end": offset + len(text),
                               "variable": variable, "format": spec})
            offset += len(text)
        # Never label spans against text whose exact construction is unknown.
        return result if "".join(part[0] for part in self.parts) == self else None


def capture_prompt(value):
    """Keep existing prompt JSON shapes; add compact, exact field-span metadata.

    Offsets are Python Unicode code points, explicitly declared for JS readers.
    Raw prompt strings are stored once. Old artifacts are never rewritten.
    """
    if isinstance(value, dict):
        result = {key: capture_prompt(item) for key, item in value.items()}
        roles = {}
        for key in ("system", "user", "base_system", "base_user"):
            prompt = value.get(key)
            if isinstance(prompt, RenderedPrompt) and (spans := prompt.spans()) is not None:
                roles[key] = spans
        if roles:
            for key in ("system", "user", "base_system", "base_user"):
                if isinstance(value.get(key), str) and key not in roles and not isinstance(value[key], RenderedPrompt):
                    roles[key] = []
            result["_provenance"] = {"version": 1, "offset_encoding": "unicode-codepoints", **roles}
        return result
    if isinstance(value, (list, tuple)):
        return [capture_prompt(item) for item in value]
    return str(value) if isinstance(value, RenderedPrompt) else value
