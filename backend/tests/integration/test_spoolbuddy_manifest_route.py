"""The SpoolBuddy PWA manifest must be served as a manifest, not as the SPA.

Without a dedicated route the catch-all answers /manifest-spoolbuddy.json with
index.html, and Chrome refuses to install /spoolbuddy as its own app.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient


@pytest.fixture
def fake_static(monkeypatch, tmp_path):
    from backend.app import main as main_mod
    from backend.app.core import config as config_mod

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>stub</title>")
    (static_dir / "manifest-spoolbuddy.json").write_text(json.dumps({"id": "/spoolbuddy"}))

    monkeypatch.setattr(config_mod.settings, "static_dir", static_dir)
    monkeypatch.setattr(main_mod.app_settings, "static_dir", static_dir)
    return static_dir


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spoolbuddy_manifest_served_as_manifest(async_client: AsyncClient, fake_static):
    resp = await async_client.get("/manifest-spoolbuddy.json")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    assert resp.json() == {"id": "/spoolbuddy"}
