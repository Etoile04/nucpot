"""Tests for API endpoints."""

import pytest
from fastapi.testclient import TestClient

from verify_service.main import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "nucpot-verify"
