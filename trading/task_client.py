"""Shared terminal client for the same authenticated endpoints used by Web."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class TaskError(RuntimeError):
    pass


class TaskClient:
    def __init__(self, server: str, token: str, timeout: float = 30):
        if not server.startswith(("http://", "https://")):
            raise TaskError("Server must use http:// or https://")
        if not token.strip():
            raise TaskError("Set LIVEOPTION_ACCESS_TOKEN or run liveoption setup")
        self.server, self.token, self.timeout = server.rstrip("/"), token.strip(), timeout

    def request(self, path: str, method: str = "GET", body=None, *, text=False):
        req = urllib.request.Request(self.server + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Do not echo request bodies, headers, credentials or server tracebacks.
            raise TaskError(f"HTTP {exc.code}: request failed ({path.split('?')[0]}). Check server logs.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TaskError("Cannot reach LiveOption. Start liveoption web or check --server.") from exc
        if text:
            return content
        try:
            return json.loads(content)
        except ValueError as exc:
            raise TaskError("Server returned an invalid JSON response") from exc

    def runs(self):
        return self.request("/api/runs")

    def presets(self):
        return self.request("/api/tasks/presets")

    def models(self):
        return self.request("/api/model-settings")

    def submit(self, spec):
        from liveoption_web.runs.protocol import RunSpecV1
        validated = RunSpecV1.model_validate(spec).model_dump(mode="json")
        return self.request("/api/tasks", "POST", validated)

    def run(self, run_id: str, section: str = ""):
        suffix = f"/{section}" if section else ""
        return self.request(f"/api/tasks/{urllib.parse.quote(run_id, safe='')}{suffix}")

    def report(self, run_id: str, format: str = "json"):
        return self.request(f"/api/tasks/{urllib.parse.quote(run_id, safe='')}/report?format={format}", text=True)
