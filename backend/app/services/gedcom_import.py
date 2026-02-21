"""GEDCOM import service — parse .ged files into Person records."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from gedcom.parser import Parser
from gedcom.element.individual import IndividualElement
from gedcom.element.family import FamilyElement

from app.models.person import Person

logger = logging.getLogger(__name__)


@dataclass
class GedcomImportResult:
    persons_created: int = 0
    persons_updated: int = 0
    persons_skipped: int = 0
    families_processed: int = 0
    root_person_id: str | None = None
    errors: list[str] = field(default_factory=list)


def import_gedcom_file(
    file_path: Path,
    db_session: Session,
    owner_gedcom_id: str | None = None,
) -> GedcomImportResult:
    """Parse a GEDCOM file, create/update Person records, compute relationships.

    Args:
        file_path: Path to the .ged file.
        db_session: SQLModel session (caller manages outer transaction).
        owner_gedcom_id: GEDCOM pointer of the owner (e.g. "@I1@").
            If provided, relationship_to_owner is computed for all persons.

    Returns:
        GedcomImportResult with counts and any errors.
    """
    result = GedcomImportResult()

    # 1. Parse
    parser = Parser()
    try:
        parser.parse_file(str(file_path), strict=False)
    except Exception as e:
        result.errors.append(f"Failed to parse GEDCOM file: {e}")
        return result

    # 2. First pass: create/update persons
    for element in parser.get_root_child_elements():
        if not isinstance(element, IndividualElement):
            continue
        _process_individual(element, db_session, result)
    db_session.commit()

    # 3. Build lookup
    persons_by_gedcom = _build_gedcom_lookup(db_session)

    # 4. Set root person
    if owner_gedcom_id and owner_gedcom_id in persons_by_gedcom:
        result.root_person_id = persons_by_gedcom[owner_gedcom_id].id

    # 5. Process families and compute relationships
    if owner_gedcom_id:
        parent_of, child_of, spouse_of = _build_family_graph(parser, result)
        _apply_relationships(
            owner_gedcom_id,
            persons_by_gedcom,
            parent_of,
            child_of,
            spouse_of,
            db_session,
            result,
        )

    return result


def _process_individual(
    element: IndividualElement,
    db: Session,
    result: GedcomImportResult,
) -> None:
    """Extract data from an IndividualElement and create/update a Person."""
    gedcom_id = element.get_pointer()

    # Extract name
    try:
        given_name, surname = element.get_name()
    except Exception:
        given_name, surname = "", ""
    name = f"{given_name} {surname}".strip()
    if not name:
        name = "Unknown"

    # Extract deceased status
    try:
        is_deceased = element.is_deceased()
    except Exception:
        is_deceased = False

    # Savepoint for error isolation
    nested = db.begin_nested()
    try:
        existing = db.exec(
            select(Person).where(Person.gedcom_id == gedcom_id)
        ).first()

        if existing:
            changed = False
            if existing.name != name:
                existing.name = name
                changed = True
            if existing.is_deceased != is_deceased:
                existing.is_deceased = is_deceased
                changed = True
            if changed:
                existing.updated_at = datetime.now(timezone.utc)
                db.add(existing)
            result.persons_updated += 1
        else:
            person = Person(
                name=name,
                gedcom_id=gedcom_id,
                is_deceased=is_deceased,
            )
            db.add(person)
            result.persons_created += 1

        db.flush()
        nested.commit()
    except Exception as e:
        nested.rollback()
        result.errors.append(f"Failed to process {gedcom_id}: {e}")
        result.persons_skipped += 1


def _build_gedcom_lookup(db: Session) -> dict[str, Person]:
    """Return dict mapping gedcom_id -> Person for all persons with a gedcom_id."""
    all_persons = db.exec(
        select(Person).where(Person.gedcom_id != None)  # noqa: E711
    ).all()
    return {p.gedcom_id: p for p in all_persons}


def _build_family_graph(
    parser: Parser,
    result: GedcomImportResult,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Build adjacency dicts from FamilyElement records.

    Returns:
        (parent_of, child_of, spouse_of) — each maps gedcom_id -> set of gedcom_ids.
    """
    parent_of: dict[str, set[str]] = {}
    child_of: dict[str, set[str]] = {}
    spouse_of: dict[str, set[str]] = {}

    for element in parser.get_root_child_elements():
        if not isinstance(element, FamilyElement):
            continue

        husb_ids = [
            h.get_pointer()
            for h in parser.get_family_members(element, "HUSB")
        ]
        wife_ids = [
            w.get_pointer()
            for w in parser.get_family_members(element, "WIFE")
        ]
        child_ids = [
            c.get_pointer()
            for c in parser.get_family_members(element, "CHIL")
        ]
        parent_ids = husb_ids + wife_ids

        for pid in parent_ids:
            parent_of.setdefault(pid, set()).update(child_ids)
        for cid in child_ids:
            child_of.setdefault(cid, set()).update(parent_ids)
        for h in husb_ids:
            for w in wife_ids:
                spouse_of.setdefault(h, set()).add(w)
                spouse_of.setdefault(w, set()).add(h)

        result.families_processed += 1

    return parent_of, child_of, spouse_of


def _compute_relationship(
    owner_id: str,
    target_id: str,
    parent_of: dict[str, set[str]],
    child_of: dict[str, set[str]],
    spouse_of: dict[str, set[str]],
) -> str:
    """Compute the relationship of target relative to owner.

    Priority order: spouse -> child -> parent -> sibling ->
    grandparent -> grandchild -> other.
    """
    # Spouse
    if target_id in spouse_of.get(owner_id, set()):
        return "spouse"

    # Child (owner is parent of target)
    if target_id in parent_of.get(owner_id, set()):
        return "child"

    # Parent (owner is child of target)
    if target_id in child_of.get(owner_id, set()):
        return "parent"

    # Sibling (shared parents)
    owner_parents = child_of.get(owner_id, set())
    siblings: set[str] = set()
    for p in owner_parents:
        siblings |= parent_of.get(p, set())
    siblings.discard(owner_id)
    if target_id in siblings:
        return "sibling"

    # Grandparent (2 hops up)
    grandparents: set[str] = set()
    for p in owner_parents:
        grandparents |= child_of.get(p, set())
    if target_id in grandparents:
        return "grandparent"

    # Grandchild (2 hops down)
    owner_children = parent_of.get(owner_id, set())
    grandchildren: set[str] = set()
    for c in owner_children:
        grandchildren |= parent_of.get(c, set())
    if target_id in grandchildren:
        return "grandchild"

    return "other"


def _apply_relationships(
    owner_gedcom_id: str,
    persons_by_gedcom: dict[str, Person],
    parent_of: dict[str, set[str]],
    child_of: dict[str, set[str]],
    spouse_of: dict[str, set[str]],
    db: Session,
    result: GedcomImportResult,
) -> None:
    """Compute and apply relationship_to_owner for all persons."""
    # Snapshot IDs before modifying session
    person_snapshots = {
        gid: {"id": p.id, "name": p.name}
        for gid, p in persons_by_gedcom.items()
    }

    for gid, snap in person_snapshots.items():
        person = db.get(Person, snap["id"])
        if person is None:
            continue

        # Don't overwrite manually-set relationships
        if person.relationship_to_owner is not None:
            continue

        if gid == owner_gedcom_id:
            person.relationship_to_owner = "self"
        else:
            person.relationship_to_owner = _compute_relationship(
                owner_gedcom_id, gid, parent_of, child_of, spouse_of,
            )

        person.updated_at = datetime.now(timezone.utc)
        db.add(person)

    db.commit()
