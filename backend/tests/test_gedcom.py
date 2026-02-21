"""Tests for GEDCOM import — person creation, deduplication, relationships, deceased, validation."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.models.person import Person
from app.services.gedcom_import import import_gedcom_file, GedcomImportResult

GEDCOM_CONTENT = textwrap.dedent("""\
    0 HEAD
    1 SOUR TEST
    1 GEDC
    2 VERS 5.5
    2 FORM LINEAGE-LINKED
    1 CHAR UTF-8
    0 @I1@ INDI
    1 NAME John /Smith/
    0 @I2@ INDI
    1 NAME Jane /Smith/
    0 @I3@ INDI
    1 NAME Tom /Smith/
    0 @I4@ INDI
    1 NAME Mary /Jones/
    0 @I5@ INDI
    1 NAME Robert /Smith/
    1 DEAT
    0 @I6@ INDI
    1 NAME Susan /Smith/
    0 @I7@ INDI
    1 NAME Alice /Smith/
    0 @F1@ FAM
    1 HUSB @I1@
    1 WIFE @I2@
    1 CHIL @I3@
    1 CHIL @I4@
    0 @F2@ FAM
    1 HUSB @I5@
    1 WIFE @I6@
    1 CHIL @I1@
    1 CHIL @I7@
    0 TRLR
""")


@pytest.fixture(name="gedcom_file")
def gedcom_file_fixture(tmp_path: Path) -> Path:
    """Write a minimal valid GEDCOM 5.5 file and return its path."""
    path = tmp_path / "test.ged"
    path.write_text(GEDCOM_CONTENT, encoding="utf-8")
    return path


@pytest.fixture(name="invalid_file")
def invalid_file_fixture(tmp_path: Path) -> Path:
    """Write a plain text file (not GEDCOM) and return its path."""
    path = tmp_path / "invalid.txt"
    path.write_text("This is not a GEDCOM file", encoding="utf-8")
    return path


def test_import_creates_persons(session: Session, gedcom_file: Path) -> None:
    """Importing a GEDCOM file creates Person records for each individual."""
    result = import_gedcom_file(gedcom_file, session)

    assert result.persons_created == 7
    assert result.persons_updated == 0
    assert result.persons_skipped == 0
    assert len(result.errors) == 0

    persons = session.exec(select(Person)).all()
    assert len(persons) == 7

    names = {p.name for p in persons}
    assert names == {
        "John Smith",
        "Jane Smith",
        "Tom Smith",
        "Mary Jones",
        "Robert Smith",
        "Susan Smith",
        "Alice Smith",
    }

    for person in persons:
        assert person.gedcom_id is not None
    gedcom_ids = {p.gedcom_id for p in persons}
    assert gedcom_ids == {"@I1@", "@I2@", "@I3@", "@I4@", "@I5@", "@I6@", "@I7@"}


def test_import_deduplicates_by_gedcom_id(session: Session, gedcom_file: Path) -> None:
    """Re-importing the same GEDCOM updates existing persons instead of duplicating."""
    result1 = import_gedcom_file(gedcom_file, session)
    assert result1.persons_created == 7

    # Modify one person's name to detect update on re-import
    john = session.exec(
        select(Person).where(Person.gedcom_id == "@I1@")
    ).one()
    john.name = "John Old"
    session.add(john)
    session.commit()

    result2 = import_gedcom_file(gedcom_file, session)
    assert result2.persons_created == 0
    assert result2.persons_updated == 7

    persons = session.exec(select(Person)).all()
    assert len(persons) == 7

    # Verify the name was updated back from the GEDCOM data
    john_updated = session.exec(
        select(Person).where(Person.gedcom_id == "@I1@")
    ).one()
    assert john_updated.name == "John Smith"


def test_import_sets_relationships(session: Session, gedcom_file: Path) -> None:
    """Importing with owner_gedcom_id computes relationship_to_owner for all persons."""
    result = import_gedcom_file(gedcom_file, session, owner_gedcom_id="@I1@")

    assert result.families_processed == 2

    persons = session.exec(select(Person)).all()
    by_gedcom = {p.gedcom_id: p for p in persons}

    assert by_gedcom["@I1@"].relationship_to_owner == "self"
    assert by_gedcom["@I2@"].relationship_to_owner == "spouse"
    assert by_gedcom["@I3@"].relationship_to_owner == "child"
    assert by_gedcom["@I4@"].relationship_to_owner == "child"
    assert by_gedcom["@I5@"].relationship_to_owner == "parent"
    assert by_gedcom["@I6@"].relationship_to_owner == "parent"
    assert by_gedcom["@I7@"].relationship_to_owner == "sibling"


def test_import_marks_deceased(session: Session, gedcom_file: Path) -> None:
    """Persons with a DEAT tag in GEDCOM are marked is_deceased=True."""
    import_gedcom_file(gedcom_file, session)

    robert = session.exec(
        select(Person).where(Person.gedcom_id == "@I5@")
    ).one()
    assert robert.is_deceased is True

    # Living persons should not be marked deceased
    for gid in ("@I1@", "@I2@"):
        person = session.exec(
            select(Person).where(Person.gedcom_id == gid)
        ).one()
        assert person.is_deceased is False


def test_import_invalid_file(client) -> None:
    """Uploading a non-.ged file to the GEDCOM endpoint returns 422."""
    resp = client.post(
        "/api/owner/gedcom",
        files={"file": ("bad.txt", b"not gedcom", "text/plain")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "GEDCOM" in detail or ".ged" in detail
