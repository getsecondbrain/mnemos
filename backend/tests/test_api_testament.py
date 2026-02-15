"""Tests for /api/testament router endpoints."""

from __future__ import annotations

import os

import pytest
from sqlmodel import Session, select

from app.main import app as fastapi_app
from app.models.testament import Heir, TestamentConfig, HeirAuditLog


# ===========================================================================
# TestTestamentConfig
# ===========================================================================


class TestTestamentConfig:
    def test_get_config_defaults(self, client, session):
        """GET /api/testament/config returns default values."""
        resp = client.get("/api/testament/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["threshold"] == 3
        assert data["total_shares"] == 5
        assert data["shares_generated"] is False

    def test_update_config(self, client, session):
        """PUT /api/testament/config updates threshold."""
        resp = client.put("/api/testament/config", json={"threshold": 4})
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 4

    def test_update_config_after_shares_generated_409(self, client, session):
        """PUT /api/testament/config fails if shares already generated."""
        # Mark shares as generated
        config = session.get(TestamentConfig, 1)
        if config is None:
            config = TestamentConfig(id=1, shares_generated=True)
            session.add(config)
        else:
            config.shares_generated = True
            session.add(config)
        session.commit()

        resp = client.put("/api/testament/config", json={"threshold": 2})
        assert resp.status_code == 409


# ===========================================================================
# TestHeirCRUD
# ===========================================================================


class TestHeirCRUD:
    def test_create_heir(self, client, session):
        """POST /api/testament/heirs creates an heir."""
        resp = client.post("/api/testament/heirs", json={
            "name": "Alice",
            "email": "alice@test.com",
            "share_index": 1,
            "role": "spouse",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["email"] == "alice@test.com"

    def test_list_heirs(self, client, session):
        """GET /api/testament/heirs returns list of heirs."""
        client.post("/api/testament/heirs", json={
            "name": "Bob", "email": "bob@test.com", "share_index": 2, "role": "friend",
        })
        resp = client.get("/api/testament/heirs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_update_heir(self, client, session):
        """PUT /api/testament/heirs/{id} updates the heir."""
        create_resp = client.post("/api/testament/heirs", json={
            "name": "Carol", "email": "carol@test.com", "share_index": 3, "role": "lawyer",
        })
        heir_id = create_resp.json()["id"]

        resp = client.put(f"/api/testament/heirs/{heir_id}", json={"name": "Carol Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Carol Updated"

    def test_delete_heir(self, client, session):
        """DELETE /api/testament/heirs/{id} removes the heir."""
        create_resp = client.post("/api/testament/heirs", json={
            "name": "Dave", "email": "dave@test.com", "share_index": 4, "role": "friend",
        })
        heir_id = create_resp.json()["id"]

        resp = client.delete(f"/api/testament/heirs/{heir_id}")
        assert resp.status_code == 200

    def test_delete_nonexistent_heir_404(self, client):
        """DELETE /api/testament/heirs/{bad_id} returns 404."""
        resp = client.delete("/api/testament/heirs/nonexistent-heir-id")
        assert resp.status_code == 404


# ===========================================================================
# TestShamirSplit
# ===========================================================================


class TestShamirSplit:
    def test_split_returns_shares(self, auth_client, session):
        """POST /api/testament/shamir/split returns 5 shares."""
        resp = auth_client.post("/api/testament/shamir/split", json={
            "passphrase": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["shares"]) == 5
        assert data["threshold"] == 3
        assert data["total_shares"] == 5

    def test_split_marks_shares_generated(self, auth_client, session):
        """After split, config shows shares_generated=True."""
        auth_client.post("/api/testament/shamir/split", json={"passphrase": ""})

        resp = auth_client.get("/api/testament/config")
        assert resp.status_code == 200
        assert resp.json()["shares_generated"] is True


# ===========================================================================
# TestHeirModeActivation
# ===========================================================================


class TestHeirModeActivation:
    def test_activate_with_valid_shares(self, auth_client, session):
        """POST /api/testament/heir-mode/activate with valid shares succeeds."""
        # Split key first
        split_resp = auth_client.post("/api/testament/shamir/split", json={"passphrase": ""})
        shares = split_resp.json()["shares"]

        # Activate with 3 shares
        resp = auth_client.post("/api/testament/heir-mode/activate", json={
            "shares": shares[:3],
            "passphrase": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "access_token" in data

    def test_activate_with_insufficient_shares_400(self, auth_client, session):
        """POST with fewer shares than threshold returns 400."""
        auth_client.post("/api/testament/shamir/split", json={"passphrase": ""})

        # Only provide 1 share (threshold is 3)
        resp = auth_client.post("/api/testament/heir-mode/activate", json={
            "shares": ["fake share one two three four five six seven eight nine ten "
                       "eleven twelve thirteen fourteen fifteen sixteen seventeen "
                       "eighteen nineteen twenty"],
            "passphrase": "",
        })
        assert resp.status_code == 400

    def test_heir_mode_status(self, client, session):
        """GET /api/testament/heir-mode/status returns active state."""
        resp = client.get("/api/testament/heir-mode/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data


# ===========================================================================
# TestAuditLog
# ===========================================================================


class TestAuditLog:
    def test_get_audit_log(self, client, session):
        """GET /api/testament/audit-log returns list."""
        resp = client.get("/api/testament/audit-log")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
