from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.config import Settings
from app.models.suggestion import LoopState

logger = logging.getLogger(__name__)


class LoopScheduler:
    """Track when each background AI loop last ran and determine which are due."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._intervals: dict[str, timedelta] = {
            "tag_suggest": timedelta(hours=settings.tag_suggest_interval_hours),
            "enrich_prompt": timedelta(hours=settings.enrich_interval_hours),
            "connection_rescan": timedelta(hours=settings.connection_rescan_interval_hours),
            "digest": timedelta(hours=settings.digest_interval_hours),
            "immich_sync": timedelta(hours=settings.immich_sync_interval_hours),
        }

    def initialize(self, engine) -> None:
        """Ensure all loop names exist in the loop_state table.

        Called once at startup from the app lifespan.
        """
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            for loop_name, interval in self._intervals.items():
                existing = session.exec(
                    select(LoopState).where(LoopState.loop_name == loop_name)
                ).first()
                if existing is None:
                    state = LoopState(
                        loop_name=loop_name,
                        next_run_at=now + interval,
                        enabled=True,
                    )
                    session.add(state)
                    logger.info(
                        "Initialized loop state: %s (next run at %s)",
                        loop_name,
                        (now + interval).isoformat(),
                    )
            session.commit()

    def check_due(self, engine) -> list[str]:
        """Return loop names that are enabled and due to run."""
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            stmt = (
                select(LoopState)
                .where(LoopState.enabled == True)  # noqa: E712
                .where(LoopState.next_run_at <= now)
            )
            due_loops = session.exec(stmt).all()
            return [loop.loop_name for loop in due_loops]

    def mark_started(self, engine, loop_name: str) -> None:
        """Update loop state after a job has been submitted."""
        now = datetime.now(timezone.utc)
        interval = self._intervals.get(loop_name)
        if interval is None:
            logger.warning("Unknown loop name: %s", loop_name)
            return

        with Session(engine) as session:
            state = session.exec(
                select(LoopState).where(LoopState.loop_name == loop_name)
            ).first()
            if state is not None:
                state.last_run_at = now
                state.next_run_at = now + interval
                session.add(state)
                session.commit()
                logger.debug(
                    "Marked loop %s as started, next run at %s",
                    loop_name,
                    state.next_run_at.isoformat(),
                )
