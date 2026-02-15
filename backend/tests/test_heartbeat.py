"""Tests for the HeartbeatService — dead man's switch."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.config import Settings
from app.models.heartbeat import Heartbeat, HeartbeatAlert
from app.services.heartbeat import HeartbeatService
from app.utils.crypto import hmac_sha256


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        heartbeat_check_interval_days=30,
        heartbeat_trigger_days=90,
        alert_email="owner@test.com",
        emergency_contact_email="emergency@test.com",
        smtp_host="",  # no SMTP — dispatch still creates alert records
    )


@pytest.fixture()
def heartbeat_service(settings: Settings) -> HeartbeatService:
    return HeartbeatService(settings)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _do_checkin(
    service: HeartbeatService,
    session: Session,
    master_key: bytes,
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """Generate a challenge and complete a valid check-in."""
    challenge_resp = service.generate_challenge(session)
    response_hmac = hmac_sha256(master_key, challenge_resp.challenge.encode("utf-8"))
    return service.verify_checkin(
        challenge=challenge_resp.challenge,
        response_hmac=response_hmac,
        master_key=master_key,
        db=session,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _insert_old_heartbeat(session: Session, days_ago: int) -> Heartbeat:
    """Insert a heartbeat row backdated by *days_ago* days.

    SQLite stores naive datetimes, so we use naive UTC to match what
    HeartbeatService.verify_checkin writes (datetime.now(timezone.utc)
    loses the tzinfo when round-tripped through SQLite/SQLModel).
    """
    hb = Heartbeat(
        checked_in_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago),
        challenge=f"test-challenge-{days_ago}",
        response_hash="test-hash",
    )
    session.add(hb)
    session.commit()
    session.refresh(hb)
    return hb


# ===========================================================================
# TestGenerateChallenge
# ===========================================================================


class TestGenerateChallenge:
    def test_returns_challenge_and_expiry(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        resp = heartbeat_service.generate_challenge(session)
        assert isinstance(resp.challenge, str)
        assert len(resp.challenge) == 64  # 32 bytes hex
        assert resp.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

    def test_generates_unique_challenges(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        c1 = heartbeat_service.generate_challenge(session)
        c2 = heartbeat_service.generate_challenge(session)
        assert c1.challenge != c2.challenge

    def test_challenge_expiry_is_correct_interval(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        resp = heartbeat_service.generate_challenge(session)
        expected = now + timedelta(days=30)
        assert abs((resp.expires_at - expected).total_seconds()) < 2


# ===========================================================================
# TestVerifyCheckin
# ===========================================================================


class TestVerifyCheckin:
    def test_valid_checkin_records_heartbeat(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        result = _do_checkin(heartbeat_service, session, master_key)
        assert result.success is True

        rows = session.exec(select(Heartbeat)).all()
        assert len(rows) == 1

    def test_checkin_resets_timer(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        result = _do_checkin(heartbeat_service, session, master_key)
        expected_next = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        assert abs((result.next_due - expected_next).total_seconds()) < 2

    def test_checkin_records_ip_and_user_agent(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        _do_checkin(
            heartbeat_service,
            session,
            master_key,
            ip_address="192.168.1.1",
            user_agent="MnemosTest/1.0",
        )
        hb = session.exec(select(Heartbeat)).first()
        assert hb is not None
        assert hb.ip_address == "192.168.1.1"
        assert hb.user_agent == "MnemosTest/1.0"

    def test_invalid_hmac_raises(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        challenge_resp = heartbeat_service.generate_challenge(session)
        with pytest.raises(ValueError, match="Invalid check-in response"):
            heartbeat_service.verify_checkin(
                challenge=challenge_resp.challenge,
                response_hmac="0000dead",
                master_key=master_key,
                db=session,
            )

    def test_unknown_challenge_raises(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        with pytest.raises(ValueError, match="Unknown or already-used challenge"):
            heartbeat_service.verify_checkin(
                challenge="nonexistent-challenge",
                response_hmac="anything",
                master_key=master_key,
                db=session,
            )

    def test_expired_challenge_raises(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        challenge_resp = heartbeat_service.generate_challenge(session)
        # Force expiry by updating the database row
        from app.models.heartbeat import HeartbeatChallenge
        db_challenge = session.exec(
            select(HeartbeatChallenge).where(
                HeartbeatChallenge.challenge == challenge_resp.challenge
            )
        ).one()
        db_challenge.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        session.add(db_challenge)
        session.commit()

        response_hmac = hmac_sha256(
            master_key, challenge_resp.challenge.encode("utf-8")
        )
        with pytest.raises(ValueError, match="Challenge has expired"):
            heartbeat_service.verify_checkin(
                challenge=challenge_resp.challenge,
                response_hmac=response_hmac,
                master_key=master_key,
                db=session,
            )

    def test_challenge_survives_service_restart(
        self,
        settings: Settings,
        session: Session,
        master_key: bytes,
    ):
        """Challenges persisted in DB survive HeartbeatService re-instantiation."""
        service1 = HeartbeatService(settings)
        challenge_resp = service1.generate_challenge(session)

        # Simulate server restart — create a new service instance
        service2 = HeartbeatService(settings)

        response_hmac = hmac_sha256(
            master_key, challenge_resp.challenge.encode("utf-8")
        )
        result = service2.verify_checkin(
            challenge=challenge_resp.challenge,
            response_hmac=response_hmac,
            master_key=master_key,
            db=session,
        )
        assert result.success is True

    def test_used_challenge_cannot_be_reused(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        """A challenge marked as used cannot be verified again."""
        challenge_resp = heartbeat_service.generate_challenge(session)
        response_hmac = hmac_sha256(
            master_key, challenge_resp.challenge.encode("utf-8")
        )
        # First use succeeds
        result = heartbeat_service.verify_checkin(
            challenge=challenge_resp.challenge,
            response_hmac=response_hmac,
            master_key=master_key,
            db=session,
        )
        assert result.success is True

        # Second use fails
        with pytest.raises(ValueError, match="Unknown or already-used challenge"):
            heartbeat_service.verify_checkin(
                challenge=challenge_resp.challenge,
                response_hmac=response_hmac,
                master_key=master_key,
                db=session,
            )

    def test_challenge_consumed_after_use(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        challenge_resp = heartbeat_service.generate_challenge(session)
        response_hmac = hmac_sha256(
            master_key, challenge_resp.challenge.encode("utf-8")
        )
        heartbeat_service.verify_checkin(
            challenge=challenge_resp.challenge,
            response_hmac=response_hmac,
            master_key=master_key,
            db=session,
        )
        with pytest.raises(ValueError, match="Unknown or already-used challenge"):
            heartbeat_service.verify_checkin(
                challenge=challenge_resp.challenge,
                response_hmac=response_hmac,
                master_key=master_key,
                db=session,
            )


# ===========================================================================
# TestGetStatus
# ===========================================================================


class TestGetStatus:
    def test_status_no_checkins(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        status = heartbeat_service.get_status(session)
        assert status.last_checkin is None
        assert status.days_since is None
        assert status.is_overdue is False

    def test_status_after_checkin(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        _do_checkin(heartbeat_service, session, master_key)
        status = heartbeat_service.get_status(session)
        assert status.last_checkin is not None
        assert status.days_since == 0
        assert status.is_overdue is False
        expected_next = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        assert status.next_due is not None
        assert abs((status.next_due - expected_next).total_seconds()) < 2

    def test_status_shows_overdue(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=35)
        status = heartbeat_service.get_status(session)
        assert status.is_overdue is True
        assert status.days_since == 35

    def test_status_includes_alert_level(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=65)
        status = heartbeat_service.get_status(session)
        assert status.current_alert_level == "contact_alert"


# ===========================================================================
# TestCheckDeadlines
# ===========================================================================


class TestCheckDeadlines:
    @pytest.mark.asyncio
    async def test_no_alerts_when_fresh(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        alerts = await heartbeat_service.check_deadlines(session)
        assert alerts == []

    @pytest.mark.asyncio
    async def test_no_alerts_when_recent_checkin(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=5)
        alerts = await heartbeat_service.check_deadlines(session)
        assert alerts == []

    @pytest.mark.asyncio
    async def test_30_day_gap_triggers_reminder(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=31)
        alerts = await heartbeat_service.check_deadlines(session)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "reminder"
        assert alerts[0].recipient == "owner"
        assert alerts[0].days_since_checkin == 31
        # Verify persisted in DB
        db_alerts = session.exec(select(HeartbeatAlert)).all()
        assert len(db_alerts) == 1

    @pytest.mark.asyncio
    async def test_45_day_gap_triggers_urgent_reminder(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=46)
        alerts = await heartbeat_service.check_deadlines(session)
        alert_types = {a.alert_type for a in alerts}
        assert "reminder" in alert_types
        assert "reminder_urgent" in alert_types
        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_60_day_gap_triggers_contact_alert(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=61)
        alerts = await heartbeat_service.check_deadlines(session)
        alert_types = {a.alert_type for a in alerts}
        assert alert_types == {"reminder", "reminder_urgent", "contact_alert"}

    @pytest.mark.asyncio
    async def test_75_day_gap_triggers_keyholder_alert(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=76)
        alerts = await heartbeat_service.check_deadlines(session)
        alert_types = {a.alert_type for a in alerts}
        assert alert_types == {
            "reminder",
            "reminder_urgent",
            "contact_alert",
            "keyholder_alert",
        }

    @pytest.mark.asyncio
    async def test_90_day_gap_triggers_inheritance(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=91)
        alerts = await heartbeat_service.check_deadlines(session)
        alert_types = {a.alert_type for a in alerts}
        assert alert_types == {
            "reminder",
            "reminder_urgent",
            "contact_alert",
            "keyholder_alert",
            "inheritance_trigger",
        }
        assert len(alerts) == 5

    @pytest.mark.asyncio
    async def test_alerts_not_duplicated(
        self, heartbeat_service: HeartbeatService, session: Session
    ):
        _insert_old_heartbeat(session, days_ago=35)
        first = await heartbeat_service.check_deadlines(session)
        assert len(first) == 1
        second = await heartbeat_service.check_deadlines(session)
        assert len(second) == 0

    @pytest.mark.asyncio
    async def test_checkin_resets_alert_cycle(
        self,
        heartbeat_service: HeartbeatService,
        session: Session,
        master_key: bytes,
    ):
        # Old heartbeat triggers reminder
        _insert_old_heartbeat(session, days_ago=35)
        alerts1 = await heartbeat_service.check_deadlines(session)
        assert len(alerts1) == 1

        # Fresh check-in resets cycle
        _do_checkin(heartbeat_service, session, master_key)

        # New old heartbeat (simulated by inserting another backdated one)
        # But the LATEST heartbeat is the fresh one, so no alerts yet
        alerts2 = await heartbeat_service.check_deadlines(session)
        assert len(alerts2) == 0


# ===========================================================================
# TestHeartbeatAPI
# ===========================================================================


class TestHeartbeatAPI:
    def test_get_status_returns_200(self, client, session, settings):
        from app.main import app as fastapi_app

        fastapi_app.state.heartbeat_service = HeartbeatService(settings)
        resp = client.get("/api/heartbeat/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "last_checkin" in data
        assert "is_overdue" in data
        assert "alerts" in data

    def test_full_checkin_flow_via_api(self, auth_client, session, settings):
        from app.main import app as fastapi_app

        fastapi_app.state.heartbeat_service = HeartbeatService(settings)

        # 1. Get challenge
        resp = auth_client.get("/api/heartbeat/challenge")
        assert resp.status_code == 200
        challenge = resp.json()["challenge"]

        # 2. Compute HMAC with master key
        master_key = auth_client._master_key
        response_hmac = hmac_sha256(master_key, challenge.encode("utf-8"))

        # 3. Check in
        resp = auth_client.post(
            "/api/heartbeat/checkin",
            json={"challenge": challenge, "response_hmac": response_hmac},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 4. Verify status updated
        resp = auth_client.get("/api/heartbeat/status")
        assert resp.status_code == 200
        assert resp.json()["last_checkin"] is not None


# ===========================================================================
# TestHeartbeatEdgeCases
# ===========================================================================


class TestHeartbeatEdgeCases:
    def test_challenge_uniqueness(self, heartbeat_service, session):
        """Two challenges should be different."""
        c1 = heartbeat_service.generate_challenge(session)
        c2 = heartbeat_service.generate_challenge(session)
        assert c1.challenge != c2.challenge

    def test_checkin_with_wrong_challenge(self, heartbeat_service, session, master_key):
        """Using a challenge that was never issued raises ValueError."""
        response_hmac = hmac_sha256(master_key, b"wrong-challenge-value")
        with pytest.raises(ValueError, match="Unknown or already-used challenge"):
            heartbeat_service.verify_checkin(
                challenge="wrong-challenge-value",
                response_hmac=response_hmac,
                master_key=master_key,
                db=session,
            )

    def test_checkin_with_wrong_key(self, heartbeat_service, session, master_key):
        """Using the wrong master key to sign the challenge fails."""
        challenge_resp = heartbeat_service.generate_challenge(session)
        wrong_key = os.urandom(32)
        wrong_hmac = hmac_sha256(wrong_key, challenge_resp.challenge.encode("utf-8"))
        with pytest.raises(ValueError, match="Invalid check-in response"):
            heartbeat_service.verify_checkin(
                challenge=challenge_resp.challenge,
                response_hmac=wrong_hmac,
                master_key=master_key,
                db=session,
            )

    def test_multiple_checkins_reset_timer(self, heartbeat_service, session, master_key):
        """Multiple check-ins each produce separate Heartbeat records."""
        _do_checkin(heartbeat_service, session, master_key)
        _do_checkin(heartbeat_service, session, master_key)
        _do_checkin(heartbeat_service, session, master_key)

        rows = session.exec(select(Heartbeat)).all()
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_all_threshold_levels(self, heartbeat_service, session, master_key):
        """Simulate 30, 45, 60, 75, 90 day gaps and check alert progression."""
        from sqlmodel import delete as sql_delete
        for days, expected_count in [(31, 1), (46, 2), (61, 3), (76, 4), (91, 5)]:
            session.exec(sql_delete(HeartbeatAlert))
            session.exec(sql_delete(Heartbeat))
            session.commit()

            _insert_old_heartbeat(session, days_ago=days)
            alerts = await heartbeat_service.check_deadlines(session)
            assert len(alerts) == expected_count, (
                f"Expected {expected_count} alerts at {days} days, got {len(alerts)}"
            )
