"""Tests for liveoption_web.config.Settings."""

from __future__ import annotations

import pytest


def test_settings_requires_internal_secret(monkeypatch):
    monkeypatch.delenv("LIVEOPTION_INTERNAL_SECRET", raising=False)
    from liveoption_web.config import Settings

    # pydantic renders the missing field as ``internal_secret``; match
    # case-insensitively so the test asserts the user-visible env var
    # name ``INTERNAL_SECRET`` (mod case) appears in the error.
    with pytest.raises(Exception, match="(?i)internal_secret"):
        Settings()


def test_settings_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "shh")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))
    from liveoption_web.config import Settings

    s = Settings()
    assert s.internal_secret.get_secret_value() == "shh"
    assert s.db_path == tmp_path / "db.sqlite"
    assert s.runs_dir == tmp_path / "runs"
