from __future__ import annotations


def test_health_endpoint(client):
    """GET /api/health should return 200 with status=healthy."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "mnemos-backend"
    assert data["version"] == "0.1.0"
    assert data["checks"]["database"] == "ok"


def test_readiness_endpoint(client):
    """GET /api/health/ready should return 200 with status=ready."""
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


# ── Edge Cases ────────────────────────────────────────────────────────


class TestHealthEdgeCases:
    def test_health_response_fields(self, client):
        """Verify response includes all expected fields."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "service" in data
        assert "checks" in data
        assert "database" in data["checks"]

    def test_health_no_auth_required(self, client_no_auth):
        """Health endpoint should work without auth."""
        resp = client_no_auth.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_readiness_no_auth_required(self, client_no_auth):
        """Readiness endpoint should work without auth."""
        resp = client_no_auth.get("/api/health/ready")
        assert resp.status_code == 200
