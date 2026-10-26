"""GET /v1/scenarios + GET /v1/me — read-only catalog and capability endpoints (Task 8)."""

from __future__ import annotations

import pytest


ALLOWED_EMAIL = "haochenluo02@gmail.com"


def _hdr(email: str | None = ALLOWED_EMAIL) -> dict[str, str]:
    h = {
        "X-Internal-Secret": "test-secret",
        "X-Actor-User": "user_test",
    }
    if email is not None:
        h["X-Actor-Email"] = email
    return h


_EXPECTED_SUPPORTED_UNDERLYINGS = {
    # Intraday
    "SPY",
    # Overlay
    "QQQ", "NVDA",
    # Earnings universe
    "AAPL", "MSFT", "TSLA", "AMZN", "META",
    "AMD", "INTC", "MU", "AVGO",
    "NFLX", "DIS", "NKE",
    "CRM", "SNOW", "PLTR",
    "HOOD", "COIN", "SOFI", "PYPL",
    "SHOP", "RKLB", "SMCI", "CELH",
}


@pytest.mark.asyncio
async def test_get_scenarios_returns_catalog_items(client):
    r = await client.get("/v1/scenarios", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "llm_model_choices" in body
    assert isinstance(body["llm_model_choices"], list)

    # Supported preset tickers.
    assert "supported_underlyings" in body
    assert isinstance(body["supported_underlyings"], list)
    assert set(body["supported_underlyings"]) == _EXPECTED_SUPPORTED_UNDERLYINGS
    assert len(body["supported_underlyings"]) == 26

    items = body["items"]
    ids = {it["id"] for it in items}
    # Assert the three paper-aligned scenarios are present.
    assert ids >= {
        "intraday_spy_2025_q1",
        "overlay_spy_qqq_2025_q1",
        "earnings_2025",
    }
    for it in items:
        assert set(it.keys()) >= {
            "id",
            "display_name",
            "description",
            "config_path",
            "overridable",
        }
        assert isinstance(it["overridable"], list)


@pytest.mark.asyncio
async def test_get_scenarios_requires_internal_secret(client):
    r = await client.get("/v1/scenarios")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_me_returns_user_id_email_and_can_launch(client):
    r = await client.get("/v1/me", headers=_hdr(ALLOWED_EMAIL))
    assert r.status_code == 200
    assert r.json() == {
        "user_id": "user_test",
        "email": ALLOWED_EMAIL,
        "can_launch": True,
    }


@pytest.mark.asyncio
async def test_get_me_can_launch_tracks_user_id_not_email(client):
    """Clerk is invite-only; auth signal is X-Actor-User (Clerk userId),
    NOT X-Actor-Email. Email may be absent if Clerk session token has
    no email claim, but can_launch should still be true."""
    r = await client.get("/v1/me", headers=_hdr(email=None))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user_test"
    assert body["email"] == ""
    assert body["can_launch"] is True


@pytest.mark.asyncio
async def test_get_me_missing_actor_user_header_returns_can_launch_false(client):
    """No X-Actor-User header → no Clerk session → can_launch=false."""
    r = await client.get(
        "/v1/me",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["can_launch"] is False
    assert body["user_id"] == ""


@pytest.mark.asyncio
async def test_get_me_email_case_normalized_to_lowercase(client):
    r = await client.get("/v1/me", headers=_hdr("HaoChenLuo02@GMAIL.com"))
    assert r.status_code == 200
    assert r.json()["email"] == ALLOWED_EMAIL
