"""Runs against the live stack: docker compose up, then pytest -m integration."""

import os

import httpx
import pytest

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration


def test_health_reports_least_privilege_users():
    resp = httpx.get(f"{BASE}/api/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["postgres"]["user"] == "app_rw"
    assert body["postgres"]["version"].startswith("18.")
    assert body["mongodb"]["user"] == "app"
    assert body["mongodb"]["replica_set"] == "rs0"
    assert body["mongodb"]["primary"] is True
