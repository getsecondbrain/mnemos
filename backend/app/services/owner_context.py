"""Owner context helper — builds owner name, family context, and people summary for RAG prompts."""

from __future__ import annotations

from sqlmodel import Session, select, func

from app.models.owner import OwnerProfile
from app.models.person import Person


def get_owner_context(db_session: Session) -> tuple[str, str, str]:
    """Return (owner_name, family_context, people_summary) for use in system prompts.

    Queries the OwnerProfile singleton, related family members, and full person stats.
    Returns ("", "", "") if no profile exists or owner name is empty.
    """
    profile = db_session.get(OwnerProfile, 1)
    if profile is None or not profile.name:
        return ("", "", "")

    owner_name = profile.name

    # Close family (those with relationship_to_owner set)
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

    # People summary — total count, breakdown by relationship, GEDCOM stats
    people_summary = _build_people_summary(db_session, owner_name)

    return (owner_name, family_context, people_summary)


def _build_people_summary(db_session: Session, owner_name: str) -> str:
    """Build a concise summary of all known people for the system prompt."""
    total = db_session.exec(
        select(func.count()).select_from(Person)
    ).one()

    if total == 0:
        return ""

    # Count by relationship
    rel_rows = db_session.exec(
        select(Person.relationship_to_owner, func.count())
        .group_by(Person.relationship_to_owner)
        .select_from(Person)
    ).all()

    rel_counts: dict[str | None, int] = {rel: cnt for rel, cnt in rel_rows}
    unclassified = rel_counts.pop(None, 0)

    # Count GEDCOM-imported (family tree) people
    gedcom_count = db_session.exec(
        select(func.count()).select_from(Person)
        .where(Person.gedcom_id != None)  # noqa: E711
    ).one()

    # Count deceased
    deceased_count = db_session.exec(
        select(func.count()).select_from(Person)
        .where(Person.is_deceased == True)  # noqa: E712
    ).one()

    # Build summary lines
    lines: list[str] = []
    lines.append(f"{owner_name} has {total} people in his network.")

    if gedcom_count:
        lines.append(f"{gedcom_count} are from the family tree (GEDCOM import).")

    if deceased_count:
        lines.append(f"{deceased_count} are deceased ancestors/relatives.")

    # Relationship breakdown (only non-null, non-self)
    classified = {k: v for k, v in rel_counts.items() if k and k != "self"}
    if classified:
        breakdown = ", ".join(f"{cnt} {rel}(s)" for rel, cnt in sorted(classified.items()))
        lines.append(f"Known relationships: {breakdown}.")

    if unclassified:
        lines.append(f"{unclassified} people have not yet been classified by relationship.")

    # Sample names (up to 30) so the LLM has some names to reference
    sample_persons = db_session.exec(
        select(Person.name, Person.relationship_to_owner)
        .order_by(Person.name)
        .limit(30)
    ).all()

    if sample_persons:
        name_parts = []
        for name, rel in sample_persons:
            if rel and rel != "self":
                name_parts.append(f"{name} ({rel})")
            else:
                name_parts.append(name)
        lines.append(f"Some known people: {'; '.join(name_parts)}.")
        if total > 30:
            lines.append(f"(Plus {total - 30} more people not listed here.)")

    return " ".join(lines)
