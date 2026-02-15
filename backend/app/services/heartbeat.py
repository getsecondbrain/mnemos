from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from hmac import compare_digest

from sqlmodel import Session, select

from app.config import Settings
from app.models.heartbeat import (
    Heartbeat,
    HeartbeatAlert,
    HeartbeatAlertRead,
    HeartbeatChallenge,
    HeartbeatChallengeResponse,
    HeartbeatCheckinResponse,
    HeartbeatStatusResponse,
)
from app.utils.crypto import hmac_sha256

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Dead man's switch — monthly cryptographic check-in.

    The owner must check in periodically by signing a cryptographic challenge
    with their master key. If they don't check in for 90 days, the
    inheritance protocol activates.
    """

    ALERT_THRESHOLDS: list[tuple[int, str, str]] = [
        (30, "reminder", "owner"),
        (45, "reminder_urgent", "owner"),
        (60, "contact_alert", "emergency_contact"),
        (75, "keyholder_alert", "all_keyholders"),
        (90, "inheritance_trigger", "all_keyholders"),
    ]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate_challenge(self, db: Session) -> HeartbeatChallengeResponse:
        """Generate a new check-in challenge, persisted to database."""
        self._cleanup_expired_challenges(db)

        challenge = os.urandom(32).hex()
        # Store as naive UTC (project convention for SQLite compatibility)
        expires_at = self._utcnow() + timedelta(
            days=self._settings.heartbeat_check_interval_days
        )

        db_challenge = HeartbeatChallenge(
            challenge=challenge,
            expires_at=expires_at,
        )
        db.add(db_challenge)
        db.commit()

        # Return expires_at with timezone for the API response
        return HeartbeatChallengeResponse(
            challenge=challenge,
            expires_at=expires_at,
        )

    def verify_checkin(
        self,
        challenge: str,
        response_hmac: str,
        master_key: bytes,
        db: Session,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> HeartbeatCheckinResponse:
        """Verify check-in by confirming the owner can sign the challenge."""
        # Look up challenge in database
        now = self._utcnow()
        db_challenge = db.exec(
            select(HeartbeatChallenge).where(
                HeartbeatChallenge.challenge == challenge,
                HeartbeatChallenge.used == False,  # noqa: E712
            )
        ).first()

        if db_challenge is None:
            raise ValueError("Unknown or already-used challenge")
        if now > db_challenge.expires_at:
            # Mark expired challenge as used to prevent reuse
            db_challenge.used = True
            db.add(db_challenge)
            db.commit()
            raise ValueError("Challenge has expired")

        # Compute expected HMAC and compare in constant time
        expected = hmac_sha256(master_key, challenge.encode("utf-8"))
        if not compare_digest(expected, response_hmac):
            raise ValueError("Invalid check-in response")

        # Record successful check-in
        heartbeat = Heartbeat(
            checked_in_at=now,
            challenge=challenge,
            response_hash=response_hmac,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(heartbeat)

        # Mark challenge as used
        db_challenge.used = True
        db.add(db_challenge)
        db.commit()

        next_due = now + timedelta(
            days=self._settings.heartbeat_check_interval_days
        )
        return HeartbeatCheckinResponse(
            success=True,
            next_due=next_due,
            message="Check-in successful",
        )

    def get_status(self, db: Session) -> HeartbeatStatusResponse:
        """Get current heartbeat status."""
        last_checkin = self._get_last_checkin(db)

        if last_checkin is None:
            # Fresh system — no check-ins yet
            recent_alerts = db.exec(
                select(HeartbeatAlert)
                .order_by(HeartbeatAlert.sent_at.desc())  # type: ignore[union-attr]
                .limit(10)
            ).all()
            return HeartbeatStatusResponse(
                last_checkin=None,
                days_since=None,
                next_due=None,
                is_overdue=False,
                current_alert_level=None,
                alerts=[HeartbeatAlertRead.model_validate(a) for a in recent_alerts],
            )

        now = self._utcnow()
        days_since = (now - last_checkin.checked_in_at).days
        is_overdue = days_since >= self._settings.heartbeat_check_interval_days
        next_due = last_checkin.checked_in_at + timedelta(
            days=self._settings.heartbeat_check_interval_days
        )

        # Find current alert level
        current_alert_level: str | None = None
        for threshold_days, alert_type, _ in self.ALERT_THRESHOLDS:
            if days_since >= threshold_days:
                current_alert_level = alert_type

        recent_alerts = db.exec(
            select(HeartbeatAlert)
            .order_by(HeartbeatAlert.sent_at.desc())  # type: ignore[union-attr]
            .limit(10)
        ).all()

        return HeartbeatStatusResponse(
            last_checkin=last_checkin.checked_in_at,
            days_since=days_since,
            next_due=next_due,
            is_overdue=is_overdue,
            current_alert_level=current_alert_level,
            alerts=[HeartbeatAlertRead.model_validate(a) for a in recent_alerts],
        )

    async def check_deadlines(self, db: Session) -> list[HeartbeatAlert]:
        """Check heartbeat deadlines and dispatch alerts if overdue.

        Called by the background worker daily (or on each worker cycle).
        """
        last_checkin = self._get_last_checkin(db)
        if last_checkin is None:
            return []

        now = self._utcnow()
        days_since = (now - last_checkin.checked_in_at).days
        new_alerts: list[HeartbeatAlert] = []

        for threshold_days, alert_type, recipient_type in self.ALERT_THRESHOLDS:
            if days_since >= threshold_days:
                # Check if this alert type was already sent after the last check-in
                existing = db.exec(
                    select(HeartbeatAlert).where(
                        HeartbeatAlert.alert_type == alert_type,
                        HeartbeatAlert.sent_at > last_checkin.checked_in_at,
                    )
                ).first()
                if existing is not None:
                    continue

                delivered = self._dispatch_alert(
                    alert_type, recipient_type, days_since
                )
                alert = HeartbeatAlert(
                    sent_at=now,
                    alert_type=alert_type,
                    days_since_checkin=days_since,
                    recipient=recipient_type,
                    delivered=delivered,
                )
                db.add(alert)
                new_alerts.append(alert)

        if new_alerts:
            db.commit()

        return new_alerts

    def _dispatch_alert(
        self, alert_type: str, recipient_type: str, days_since: int
    ) -> bool:
        """Dispatch an alert email. Returns True if delivered."""
        recipients = self._get_recipients(recipient_type)
        if not recipients:
            logger.warning(
                "No recipients configured for %s, skipping alert %s",
                recipient_type,
                alert_type,
            )
            return False

        subject, body = self._compose_alert(alert_type, days_since)

        all_delivered = True
        for recipient in recipients:
            if not self._send_email(recipient, subject, body):
                all_delivered = False

        return all_delivered

    def _get_recipients(self, recipient_type: str) -> list[str]:
        """Resolve recipient type to email addresses."""
        if recipient_type == "owner":
            return [self._settings.alert_email] if self._settings.alert_email else []
        elif recipient_type == "emergency_contact":
            return (
                [self._settings.emergency_contact_email]
                if self._settings.emergency_contact_email
                else []
            )
        elif recipient_type == "all_keyholders":
            emails: list[str] = []
            if self._settings.alert_email:
                emails.append(self._settings.alert_email)
            if self._settings.emergency_contact_email:
                emails.append(self._settings.emergency_contact_email)
            return emails
        return []

    def _compose_alert(
        self, alert_type: str, days_since: int
    ) -> tuple[str, str]:
        """Compose email subject and body for an alert type."""
        if alert_type == "reminder":
            subject = (
                f"Mnemos check-in reminder — {days_since} days since last check-in"
            )
            body = (
                f"This is a reminder to check in with your Mnemos second brain.\n\n"
                f"It has been {days_since} days since your last check-in.\n"
                f"Please log in and complete your check-in to reset the timer."
            )
        elif alert_type == "reminder_urgent":
            subject = f"URGENT: Mnemos check-in overdue — {days_since} days"
            body = (
                f"URGENT: Your Mnemos check-in is overdue.\n\n"
                f"It has been {days_since} days since your last check-in.\n"
                f"If you do not check in, your emergency contacts will be "
                f"notified at 60 days and the inheritance protocol will "
                f"activate at 90 days."
            )
        elif alert_type == "contact_alert":
            subject = (
                f"Welfare check requested for Mnemos owner — "
                f"{days_since} days without check-in"
            )
            body = (
                f"You are receiving this message because you are listed as an "
                f"emergency contact for a Mnemos second brain owner.\n\n"
                f"It has been {days_since} days since their last check-in.\n"
                f"Please check on them and encourage them to log in."
            )
        elif alert_type == "keyholder_alert":
            remaining = 90 - days_since
            subject = (
                f"Mnemos inheritance protocol may activate in {remaining} days"
            )
            body = (
                f"You are receiving this message because you hold a Shamir "
                f"secret share for a Mnemos second brain.\n\n"
                f"It has been {days_since} days since the owner's last check-in.\n"
                f"The inheritance protocol will activate in {remaining} days "
                f"if no check-in occurs."
            )
        elif alert_type == "inheritance_trigger":
            subject = (
                f"Mnemos inheritance protocol activated — "
                f"{days_since} days without check-in"
            )
            body = (
                f"The Mnemos inheritance protocol has been activated.\n\n"
                f"It has been {days_since} days since the owner's last check-in.\n"
                f"Please coordinate with other key holders to combine your "
                f"Shamir secret shares and access the vault."
            )
        else:
            subject = f"Mnemos alert: {alert_type}"
            body = f"Alert type: {alert_type}\nDays since check-in: {days_since}"

        return subject, body

    def _send_email(self, to: str, subject: str, body: str) -> bool:
        """Send an email via SMTP. Returns True on success."""
        if not self._settings.smtp_host:
            logger.warning("SMTP not configured, cannot send alert to %s", to)
            return False

        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self._settings.smtp_user
            msg["To"] = to

            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port
            ) as server:
                server.starttls()
                server.login(
                    self._settings.smtp_user, self._settings.smtp_password
                )
                server.send_message(msg)

            logger.info("Alert email sent to %s: %s", to, subject)
            return True
        except Exception:
            logger.exception("Failed to send alert email to %s", to)
            return False

    def _get_last_checkin(self, db: Session) -> Heartbeat | None:
        """Get the most recent check-in record."""
        return db.exec(
            select(Heartbeat)
            .order_by(Heartbeat.checked_in_at.desc())  # type: ignore[union-attr]
            .limit(1)
        ).first()

    @staticmethod
    def _utcnow() -> datetime:
        """Return current UTC time as a naive datetime.

        SQLite (via SQLModel) strips timezone info on round-trip, so all
        stored timestamps are naive-UTC.  Using naive-UTC everywhere
        avoids "can't subtract offset-naive and offset-aware" errors.
        """
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _cleanup_expired_challenges(self, db: Session) -> None:
        """Remove expired challenges to prevent unbounded table growth."""
        now = self._utcnow()
        expired = db.exec(
            select(HeartbeatChallenge).where(
                HeartbeatChallenge.expires_at < now
            )
        ).all()
        for c in expired:
            db.delete(c)
        if expired:
            db.commit()
