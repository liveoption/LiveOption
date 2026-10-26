"""POST /v1/runs — scenario-catalog launch endpoint (Task 7)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


ALLOWED_EMAIL = "haochenluo02@gmail.com"


def _hdr(email: str = ALLOWED_EMAIL) -> dict[str, str]:
    return {
        "X-Internal-Secret": "test-secret",
        "X-Actor-User": "user_test",
        "X-Actor-Email": email,
    }


@pytest.fixture
def mock_spawn(monkeypatch):
    mock = AsyncMock(return_value=12345)
    monkeypatch.setattr("liveoption_web.api.launch.spawn_batch", mock)
    return mock


@pytest.mark.asyncio
async def test_post_runs_accepts_any_signed_in_email(client, mock_spawn):
    """Clerk is invite-only; any authenticated session may launch.
    Covers the 2026-04-24 switch from allowlist to 'any signed-in user'."""
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers=_hdr("stranger@example.com"),
    )
    assert r.status_code == 202, r.text
    mock_spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_runs_accepts_missing_email_when_user_id_present(
    client, mock_spawn
):
    """Auth signal is X-Actor-User (Clerk userId), not email. Email is
    optional — Clerk doesn't put it in the session token by default."""
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers={
            "X-Internal-Secret": "test-secret",
            "X-Actor-User": "user_test",
            # deliberately no X-Actor-Email
        },
    )
    assert r.status_code == 202, r.text
    mock_spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_runs_rejects_missing_user_id(client, mock_spawn):
    """No X-Actor-User header → no Clerk session → 403."""
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers={
            "X-Internal-Secret": "test-secret",
            "X-Actor-Email": "someone@example.com",
            # deliberately no X-Actor-User
        },
    )
    assert r.status_code == 403
    body = r.json()
    assert "not authorized" in json.dumps(body).lower()
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_post_runs_rejects_unknown_scenario(client, mock_spawn):
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "does_not_exist"},
        headers=_hdr(),
    )
    assert r.status_code == 400
    assert "unknown" in json.dumps(r.json()).lower()
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_post_runs_rejects_invalid_llm_model(client, mock_spawn):
    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "intraday_spy_2025_q1",
            "overrides": {"llm_model": "not-a-model"},
        },
        headers=_hdr(),
    )
    # Pydantic validation error on the request body → 422.
    assert r.status_code in (400, 422)
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_post_runs_happy_path_returns_202_and_composes_argv(
    client, mock_spawn
):
    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "intraday_spy_2025_q1",
            "overrides": {"bankroll": 150000, "llm_model": "gemini-2.5-flash"},
        },
        headers=_hdr(),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"].startswith("run_")

    assert mock_spawn.await_count == 1
    kwargs = mock_spawn.await_args.kwargs
    argv = kwargs["argv"]
    assert "--config" in argv
    assert "configs/scenarios/intraday_spy_2025_q1.yaml" in argv
    assert "--bankroll" in argv
    assert "150000.0" in argv
    assert "--llm-model" in argv
    assert "gemini-2.5-flash" in argv
    assert "--web-ingest" in argv
    assert "--run-id" in argv
    assert kwargs["run_id"] == body["run_id"]

    # Verify launch_spec_json persisted on the row.
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db

    settings = get_settings()
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT kind, status, scenario, created_by, launch_spec_json "
            "FROM runs WHERE id=?",
            (body["run_id"],),
        )
        row = await cur.fetchone()
    assert row is not None
    kind, status, scenario, created_by, spec_json = row
    assert kind == "batch"
    assert status == "pending"
    assert scenario == "intraday_spy_2025_q1"
    assert created_by == ALLOWED_EMAIL
    snap = json.loads(spec_json)
    assert snap["scenario_id"] == "intraday_spy_2025_q1"
    assert snap["overrides"]["bankroll"] == 150000


@pytest.mark.asyncio
async def test_post_runs_surfaces_cap_exceeded_as_429(client, monkeypatch):
    from liveoption_web.runs.supervisor import RunCapExceeded

    monkeypatch.setattr(
        "liveoption_web.api.launch.spawn_batch",
        AsyncMock(side_effect=RunCapExceeded("max 4 concurrent runs")),
    )
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers=_hdr(),
    )
    assert r.status_code == 429
    body_str = json.dumps(r.json()).lower()
    assert "concurrency" in body_str or "cap" in body_str


@pytest.mark.asyncio
async def test_post_runs_writes_prompt_file_when_override_supplied(
    client, mock_spawn, tmp_path, monkeypatch
):
    from liveoption_web.api import launch as launch_module

    fake_web_runs = tmp_path / "fake-web-runs"
    monkeypatch.setattr(launch_module, "WEB_RUNS_DIR", fake_web_runs)

    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "intraday_spy_2025_q1",
            "overrides": {"prompt_override": "You are a careful trader."},
        },
        headers=_hdr(),
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    prompt_file = fake_web_runs / f"{run_id}.prompt"
    assert prompt_file.is_file()
    assert prompt_file.read_text(encoding="utf-8") == "You are a careful trader."

    # argv must reference the file via @-prefix (T3 CLI contract).
    argv = mock_spawn.await_args.kwargs["argv"]
    assert any(a == f"@{prompt_file}" for a in argv), argv


@pytest.mark.asyncio
async def test_post_runs_unlinks_prompt_file_on_non_cap_spawn_failure(
    client, tmp_path, monkeypatch
):
    """Any spawn failure — not just RunCapExceeded — must clean up the
    orphaned .web-runs/<run_id>.prompt file so hot-path leaks don't
    accumulate under .web-runs/."""

    fake_web_runs = tmp_path / "fake-web-runs"
    monkeypatch.setattr("liveoption_web.api.launch.WEB_RUNS_DIR", fake_web_runs)
    # Simulate any non-cap spawn failure (bad python path, EACCES, etc.).
    monkeypatch.setattr(
        "liveoption_web.api.launch.spawn_batch",
        AsyncMock(side_effect=OSError("mock spawn failure")),
    )

    # Under httpx ASGI transport, non-HTTPException errors re-raise; we
    # don't care about the surface form, only that the handler reached
    # the cleanup branch before propagating.
    with pytest.raises(OSError, match="mock spawn failure"):
        await client.post(
            "/v1/runs",
            json={
                "scenario_id": "intraday_spy_2025_q1",
                "overrides": {"prompt_override": "Secret research IP"},
            },
            headers=_hdr(),
        )

    if fake_web_runs.exists():
        leftovers = list(fake_web_runs.glob("*.prompt"))
        assert not leftovers, f"leaked prompt files: {leftovers}"


@pytest.mark.asyncio
async def test_post_runs_corrupt_scenario_yaml_surfaces_500_problem_json(
    client, mock_spawn, monkeypatch
):
    """If a catalog-listed scenario YAML is corrupt, fail loud with 500
    problem+json so operators see the deploy defect — don't silently
    accept the launch with garbage row metadata."""
    from liveoption_web.api import launch as launch_module

    def _raise_corrupt(_config_path: str):
        raise launch_module.ScenarioConfigCorruptError(
            "mock: scenario yaml failed parse"
        )

    monkeypatch.setattr(launch_module, "_peek_scenario_yaml", _raise_corrupt)

    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers=_hdr(),
    )
    assert r.status_code == 500, r.text
    # spawn_batch must NOT have been called — we bailed before INSERT.
    assert mock_spawn.await_count == 0
    body = json.dumps(r.json()).lower()
    assert "scenario" in body and "corrupt" in body


# ---------------------------------------------------------------------------
# Phase C3 — family-aware override validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_runs_intraday_accepts_llm_model(client, mock_spawn):
    """Intraday is the LLM-driven family — llm_model is allowed."""
    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "intraday_spy_2025_q1",
            "overrides": {"llm_model": "deepseek-chat"},
        },
        headers=_hdr(),
    )
    assert r.status_code == 202, r.text
    mock_spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_runs_rejects_unknown_field_with_422(client, mock_spawn):
    """extra='forbid' on the family override model rejects unknown fields."""
    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "intraday_spy_2025_q1",
            "overrides": {"definitely_not_a_field": 1},
        },
        headers=_hdr(),
    )
    assert r.status_code == 422, r.text
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_post_runs_rejects_intraday_specific_field_on_overlay_family(
    client, mock_spawn
):
    """Overlay-family scenarios reject intraday-specific fields like dte_range."""
    r = await client.post(
        "/v1/runs",
        json={
            "scenario_id": "overlay_spy_qqq_2025_q1",
            "overrides": {"dte_range": [0, 7]},
        },
        headers=_hdr(),
    )
    assert r.status_code == 422, r.text
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_post_runs_persists_family_on_runs_row(client, mock_spawn):
    """The runs.family column captures the scenario's family at INSERT."""
    r = await client.post(
        "/v1/runs",
        json={"scenario_id": "intraday_spy_2025_q1"},
        headers=_hdr(),
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db

    settings = get_settings()
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT family FROM runs WHERE id=?", (run_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "intraday"
