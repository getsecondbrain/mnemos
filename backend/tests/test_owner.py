from __future__ import annotations

from app.models.person import Person


def test_get_owner_profile_creates_default(client):
    """GET /api/owner/profile returns empty profile on first call."""
    response = client.get("/api/owner/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == ""
    assert data["date_of_birth"] is None
    assert data["bio"] is None
    assert data["person_id"] is None
    assert data["updated_at"] is not None


def test_update_owner_profile(client):
    """PUT /api/owner/profile updates name/dob/bio."""
    response = client.put(
        "/api/owner/profile",
        json={"name": "Alice", "date_of_birth": "1990-05-15", "bio": "Test bio"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Alice"
    assert data["date_of_birth"] == "1990-05-15"
    assert data["bio"] == "Test bio"

    # Verify persistence via GET
    get_resp = client.get("/api/owner/profile")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["name"] == "Alice"
    assert get_data["date_of_birth"] == "1990-05-15"
    assert get_data["bio"] == "Test bio"


def test_update_owner_profile_links_person(client, session):
    """PUT with person_id sets that Person's relationship_to_owner='self'."""
    person = Person(name="Owner Person")
    session.add(person)
    session.commit()
    session.refresh(person)

    response = client.put("/api/owner/profile", json={"person_id": person.id})
    assert response.status_code == 200
    assert response.json()["person_id"] == person.id

    session.refresh(person)
    assert person.relationship_to_owner == "self"


def test_get_owner_family(client, session):
    """GET /api/owner/family returns persons with relationship set, excludes 'self'."""
    persons = [
        Person(name="Spouse", relationship_to_owner="spouse"),
        Person(name="Child", relationship_to_owner="child"),
        Person(name="Self", relationship_to_owner="self"),
    ]
    session.add_all(persons)
    session.commit()

    response = client.get("/api/owner/family")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    names = {p["name"] for p in data}
    assert names == {"Spouse", "Child"}
    assert "Self" not in names


def test_family_excludes_unrelated_persons(client, session):
    """Persons without relationship_to_owner are excluded from family."""
    persons = [
        Person(name="Family Member", relationship_to_owner="parent"),
        Person(name="Random Person", relationship_to_owner=None),
        Person(name="Another Random", relationship_to_owner=None),
    ]
    session.add_all(persons)
    session.commit()

    response = client.get("/api/owner/family")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Family Member"
