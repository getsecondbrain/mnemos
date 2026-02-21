"""Owner context helper — builds owner name and family context for RAG prompts."""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.owner import OwnerProfile
from app.models.person import Person


def get_owner_context(db_session: Session) -> tuple[str, str]:
    """Return (owner_name, family_context) for use in system prompts.

    Queries the OwnerProfile singleton and related family members.
    Returns ("", "") if no profile exists or owner name is empty.
    """
    profile = db_session.get(OwnerProfile, 1)
    if profile is None or not profile.name:
        return ("", "")

    owner_name = profile.name

    statement = (
        select(Person)
        .where(Person.relationship_to_owner != None)  # noqa: E711
        .where(Person.relationship_to_owner != "self")
        .order_by(Person.relationship_to_owner, Person.name)  # type: ignore[arg-type]
    )
    persons = db_session.exec(statement).all()

    parts: list[str] = []
    for p in persons:
        entry = f"{p.name} ({p.relationship_to_owner}"
        if p.is_deceased:
            entry += ", deceased"
        entry += ")"
        parts.append(entry)

    family_context = "; ".join(parts)
    return (owner_name, family_context)
