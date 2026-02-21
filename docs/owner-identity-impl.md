# Mnemos — Owner Identity, Family Tree, Chat Intelligence

## Context

Mnemos is a single-user encrypted second brain. Currently, the LLM has no idea who owns it — the system prompt says "digital memory of a person" generically. It also doesn't know today's date, so it can't answer temporal questions like "what happened a year ago." Conversation titles are just the first 80 chars of the user's first message, not AI-generated. This plan adds owner identity, family tree, date awareness, and smart conversation titles.

---

## Epic 1: Owner Profile (Backend)

### Task 1.1: Create OwnerProfile model

**New file**: `backend/app/models/owner.py`

Create a singleton config table following the `TestamentConfig` pattern (see `backend/app/models/testament.py:30-41`):

```python
class OwnerProfile(SQLModel, table=True):
    __tablename__ = "owner_profile"
    id: int = Field(default=1, primary_key=True)
    name: str = Field(default="")
    date_of_birth: str | None = Field(default=None)  # ISO date
    bio: str | None = Field(default=None)
    person_id: str | None = Field(default=None, foreign_key="persons.id")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Add Pydantic schemas: `OwnerProfileRead`, `OwnerProfileUpdate`.

### Task 1.2: Extend Person model with relationship fields

**Modify**: `backend/app/models/person.py`

Add three columns to the `Person` class (after line 42):
- `relationship_to_owner: str | None = Field(default=None, index=True)` — values: "self", "spouse", "child", "parent", "sibling", "grandparent", "grandchild", "aunt_uncle", "cousin", "in_law", "friend", "other"
- `is_deceased: bool = Field(default=False)`
- `gedcom_id: str | None = Field(default=None)` — GEDCOM `@Ixx@` identifier for dedup

Update all Pydantic schemas in same file:
- `PersonCreate` (line 48): add `relationship_to_owner: str | None = None`, `is_deceased: bool = False`
- `PersonUpdate` (line 55): add `relationship_to_owner: str | None = None`, `is_deceased: bool | None = None`
- `PersonRead` (line 61): add `relationship_to_owner: str | None`, `is_deceased: bool`

### Task 1.3: Register model and add migrations

**Modify**: `backend/app/models/__init__.py` — add `from app.models.owner import OwnerProfile  # noqa: F401`

**Modify**: `backend/app/db.py` — add to `_run_migrations()` after line 66:
```python
if "persons" in insp.get_table_names():
    person_cols = [c["name"] for c in insp.get_columns("persons")]
    for col_name, col_def in [
        ("relationship_to_owner", "TEXT"),
        ("is_deceased", "INTEGER DEFAULT 0"),
        ("gedcom_id", "TEXT"),
    ]:
        if col_name not in person_cols:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE persons ADD COLUMN {col_name} {col_def}"))
```

The `owner_profile` table is auto-created by `SQLModel.metadata.create_all(engine)` since the model import is registered.

### Task 1.4: Create owner router

**New file**: `backend/app/routers/owner.py`

Endpoints (all `Depends(require_auth)`):

| Method | Path | Response | Notes |
|--------|------|----------|-------|
| GET | `/api/owner/profile` | `OwnerProfileRead` | Lazy-creates singleton (pattern from testament.py) |
| PUT | `/api/owner/profile` | `OwnerProfileRead` | Updates name/dob/bio/person_id. When setting person_id, mark that Person's relationship_to_owner="self" |
| GET | `/api/owner/family` | `list[PersonRead]` | Query `WHERE relationship_to_owner IS NOT NULL AND relationship_to_owner != 'self'` ordered by relationship, name |
| POST | `/api/owner/gedcom` | `GedcomImportResult` | GEDCOM file upload (see Epic 3) |

Helper: `_get_or_create_profile(db: Session) -> OwnerProfile` — same pattern as `_get_or_create_config` in testament router.

**Modify**: `backend/app/main.py` — import and register: `app.include_router(owner.router)`

### Task 1.5: Update persons router for new fields

**Modify**: `backend/app/routers/persons.py`

In `create_person` (~line 37): pass `relationship_to_owner` and `is_deceased` from `PersonCreate` to the `Person` constructor.

In `update_person` (~line 181): handle `relationship_to_owner` and `is_deceased` updates from `PersonUpdate`.

---

## Epic 2: Chat Intelligence (Date, Identity, Smart Titles)

### Task 2.1: Add date and owner context to system prompt

**Modify**: `backend/app/services/rag.py`

Replace the static `SYSTEM_PROMPT` (lines 22-35) with a template and builder method:

```python
SYSTEM_PROMPT_TEMPLATE = """{owner_preamble}Today is {today}.

You are {owner_possessive} personal memory assistant. You have access to their
memories, notes, documents, and life experiences. Answer questions based on the retrieved
context below. If you don't have relevant memories, say so honestly.

Always cite which memories you're drawing from. Distinguish between:
- ORIGINAL SOURCE: Direct quotes or information from the person's own memories
- CONNECTION: Your inference about how memories relate to each other

Pay close attention to [Tags: ...] annotations on memories — these are user-assigned labels
that describe people, pets, topics, or categories. When asked about a tag name (e.g. a
nickname or label), use the tagged memory as context for your answer.
{family_block}
Retrieved memories:
{context}"""
```

Add imports: `from datetime import datetime, timezone`

Update `RAGService`:
- Add to `__slots__` (line 56): `"owner_name"`, `"family_context"`
- Extend `__init__` (line 58) with: `owner_name: str = ""`, `family_context: str = ""`
- Add method `_build_system_prompt(self, context: str) -> str`:
  - `today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y").replace(" 0", " ")` — e.g. "Thursday, February 20, 2026"
  - `owner_preamble`: if owner_name set, `"This is {name}'s second brain. "`, else `""`
  - `owner_possessive`: if owner_name set, `"{name}'s"`, else `"the owner's"`
  - `family_block`: if family_context set, `"\nFamily: {family_context}\n"`, else `""`
  - Format and return `SYSTEM_PROMPT_TEMPLATE`

Replace `SYSTEM_PROMPT.format(context=context)` at:
- Line 97 in `query()` → `self._build_system_prompt(context)`
- Line 147 in `build_stream()` → `self._build_system_prompt(context)`

### Task 2.2: Build owner context helper

**New file**: `backend/app/services/owner_context.py`

```python
def get_owner_context(db_session: Session) -> tuple[str, str]:
    """Return (owner_name, family_context) from the database.

    owner_name: e.g. "Silviu" or "" if not configured
    family_context: e.g. "Ana (spouse), Maria (child), Ion (parent, deceased)" or ""
    """
```

Implementation:
1. Query `OwnerProfile` singleton (id=1). If not found or name empty, return `("", "")`.
2. Query `Person WHERE relationship_to_owner IS NOT NULL AND relationship_to_owner != 'self'` ordered by relationship, name.
3. Build family_context string: `"{name} ({relationship})"` for each, appending `, deceased` if `is_deceased`.
4. Return `(profile.name, family_str)`.

### Task 2.3: Wire owner context into chat router

**Modify**: `backend/app/routers/chat.py`

At the RAGService construction (line 204-209):

```python
from app.services.owner_context import get_owner_context

owner_name, family_context = get_owner_context(db_session)
rag_service = RAGService(
    embedding_service=embedding_service,
    llm_service=llm_service,
    encryption_service=encryption_service,
    db_session=db_session,
    owner_name=owner_name,
    family_context=family_context,
)
```

### Task 2.4: AI-generated conversation titles

**Modify**: `backend/app/routers/chat.py`

Add `import asyncio` at top.

Add async function `_generate_title(llm_service, websocket, conversation_id, user_text, assistant_text, db_session)`:
- Build a short prompt: `"Generate a short descriptive title (3-7 words) for this conversation:\n\nUser: {user_text[:200]}\nAssistant: {assistant_text[:300]}\n\nRules:\n- Output ONLY the title\n- No quotes, no period at end\n- Be specific, not generic\n- Max 7 words"`
- System: `"You are a title generator. Output only a short title."`
- Call `llm_service.generate(prompt=prompt, system=system, temperature=0.3)`
- Clean the response: strip quotes, trailing punctuation, validate 2-80 chars
- Fallback: if generation fails or empty, keep the truncated title already set
- Update `conv.title` in DB
- Send `{"type": "title_update", "conversation_id": id, "title": title}` via WebSocket (wrapped in try/except for closed connections)
- Log success/failure

Modify `_persist_exchange` (line 111):
- Change return type to `tuple[bool, bool]` — `(saved, needs_ai_title)`
- Track `needs_ai_title = conv.title == "New conversation"` before setting fallback
- Still set fallback title `conv.title = user_text[:80].strip()` immediately
- Return `(True, needs_ai_title)` on success

In the message loop (~line 240-258), after `_persist_exchange`:
```python
saved, needs_ai_title = _persist_exchange(...)
if saved and needs_ai_title:
    asyncio.create_task(_generate_title(
        llm_service, websocket, conversation_id,
        text, response_text, db_session,
    ))
```

### Task 2.5: Frontend — handle title_update WebSocket message

**Modify**: `frontend/src/types/index.ts`

Line 143: add `"title_update"` to `ChatMessageType`:
```typescript
export type ChatMessageType = "auth" | "question" | "token" | "sources" | "done" | "warning" | "error" | "title_update";
```

**Modify**: `frontend/src/components/Chat.tsx`

In the `ws.onmessage` switch (line 96-150), add case before the default:
```typescript
case "title_update": {
  const convId = typeof data.conversation_id === "string" ? data.conversation_id : null;
  const newTitle = typeof data.title === "string" ? data.title : null;
  if (convId && newTitle) {
    setConversations((prev) =>
      prev.map((c) => (c.id === convId ? { ...c, title: newTitle } : c))
    );
  }
  break;
}
```

The existing `listConversations()` refresh on `done` (line 140) can remain as a safety net.

---

## Epic 3: GEDCOM Family Tree Import

### Task 3.1: Add python-gedcom dependency

**Modify**: `backend/requirements.txt` — add `python-gedcom>=1.0,<2.0`

### Task 3.2: Create GEDCOM import service

**New file**: `backend/app/services/gedcom_import.py`

```python
@dataclass
class GedcomImportResult:
    persons_created: int
    persons_updated: int
    persons_skipped: int
    families_processed: int
    root_person_id: str | None
    errors: list[str]

def import_gedcom_file(
    file_path: Path,
    db_session: Session,
    owner_gedcom_id: str | None = None,
) -> GedcomImportResult:
```

Implementation:
1. Parse `.ged` file with `gedcom.parser.Parser`
2. Extract all `IndividualElement` records — name, birth date, death date/status
3. For each individual: create or update `Person` record (dedup by `gedcom_id`)
   - Set `name` from GEDCOM name (strip slashes from surname)
   - Set `is_deceased` if death record exists
   - Set `gedcom_id` to the GEDCOM `@Ixx@` identifier
4. Extract `FamilyElement` records to map relationships
5. If `owner_gedcom_id` provided, compute relationships relative to the owner:
   - HUSB/WIFE in same FAMS → "spouse"
   - CHIL in a FAM where owner is HUSB/WIFE → "child"
   - HUSB/WIFE in a FAM where owner is CHIL → "parent"
   - Shares FAMC with owner → "sibling"
   - Parent of parent → "grandparent"
   - Child of child → "grandchild"
   - Deeper than 2 hops → "other"
6. Return `GedcomImportResult` summary

### Task 3.3: Wire GEDCOM upload endpoint

Already defined in Task 1.4 as `POST /api/owner/gedcom`. Implementation:
- Accept `UploadFile` + optional `owner_gedcom_id` query param
- Validate `.ged` extension
- Save to tmp, parse, clean up
- Return `GedcomImportResult`

---

## Epic 4: Frontend — Settings UI for Owner Identity

### Task 4.1: Add TypeScript types

**Modify**: `frontend/src/types/index.ts`

Add at end:
```typescript
export interface OwnerProfile {
  name: string;
  date_of_birth: string | null;
  bio: string | null;
  person_id: string | null;
  updated_at: string;
}

export interface OwnerProfileUpdate {
  name?: string;
  date_of_birth?: string;
  bio?: string;
  person_id?: string;
}

export interface GedcomImportResult {
  persons_created: number;
  persons_updated: number;
  persons_skipped: number;
  families_processed: number;
  root_person_id: string | null;
  errors: string[];
}
```

Update `Person` interface (line 287): add `relationship_to_owner: string | null;` and `is_deceased: boolean;`

Update `PersonCreate` (line 304): add `relationship_to_owner?: string;` and `is_deceased?: boolean;`

Update `PersonUpdate` (line 312): add `relationship_to_owner?: string;` and `is_deceased?: boolean;`

### Task 4.2: Add API functions

**Modify**: `frontend/src/services/api.ts`

Add functions:
```typescript
export async function getOwnerProfile(): Promise<OwnerProfile>
export async function updateOwnerProfile(body: OwnerProfileUpdate): Promise<OwnerProfile>
export async function getOwnerFamily(): Promise<Person[]>
export async function importGedcom(file: File, ownerGedcomId?: string): Promise<GedcomImportResult>
```

The `importGedcom` function uses `FormData` with `fetch` (same pattern as existing file upload functions).

### Task 4.3: Add Owner Identity section to Settings

**Modify**: `frontend/src/components/Settings.tsx`

Add a new "Owner Identity" section at the TOP of the settings page (before System Health). This is the most personal configuration.

**Section layout:**
```
Owner Identity
─────────────────────────────────
Your Name:     [______________] [Save]
Date of Birth: [______________] (optional)
Bio:           [______________] (optional)

Family Members
  Ana (spouse)                [Edit] [×]
  Maria (child)               [Edit] [×]
  Ion (parent, deceased)      [Edit] [×]
  [+ Add Family Member]

GEDCOM Import
  Upload a .ged file to populate your family tree.
  [Choose File...] [Import]
  (result summary shown after import)
```

**State additions:**
- `ownerProfile`, `ownerName`, `ownerDob`, `ownerBio`, `ownerSaving`
- `familyMembers` (Person[] with relationship_to_owner set)
- `gedcomFile`, `gedcomImporting`, `gedcomResult`
- `showAddFamily` (boolean toggle for add-family form)

**Load in existing useEffect:** Add `getOwnerProfile().catch(() => null)` and `getOwnerFamily().catch(() => [])` to the `Promise.all` that already loads health, heartbeat, testament, heirs, loops.

**Add Family Member form** (inline, not modal):
- Name input (text)
- Relationship dropdown: spouse, child, parent, sibling, grandparent, grandchild, aunt_uncle, cousin, in_law, friend, other
- Deceased checkbox
- Save: calls `createPerson({ name, relationship_to_owner, is_deceased })`, then refreshes family list
- Also allow linking an existing Person: small autocomplete that filters existing persons without relationships

**Edit**: Clicking Edit on a family member opens inline edit with same fields. Calls `updatePerson()`.

**Remove**: Calls `updatePerson(id, { relationship_to_owner: null })` — doesn't delete the Person, just removes the family relationship.

### Task 4.4: Show relationship badges on People page

**Modify**: `frontend/src/components/People.tsx`

In the person card, after the name, show a small badge if `relationship_to_owner` is set:
```tsx
{person.relationship_to_owner && person.relationship_to_owner !== "self" && (
  <span className="text-xs text-blue-400 bg-blue-900/30 px-1.5 py-0.5 rounded ml-1">
    {person.relationship_to_owner.replace("_", " ")}
  </span>
)}
{person.is_deceased && (
  <span className="text-xs text-gray-500 ml-1">(deceased)</span>
)}
```

---

## Epic 5: Enrich Background AI with Owner Context

### Task 5.1: Add owner context to enrichment prompts

**Modify**: `backend/app/worker.py`

In `_generate_enrich_prompt_for_memory` (line 1319), the system prompt at line 1426 should be prefixed with owner name:

```python
# Fetch owner context once per loop run (cache for the batch)
owner_name = self._cached_owner_name(engine)
name_prefix = f"You are {owner_name}'s memory assistant. " if owner_name else ""
system = f"{name_prefix}You are a thoughtful memory assistant. Output only a single question, under 20 words. No preamble, no quotes."
```

Add helper `_cached_owner_name(self, engine)` that reads `OwnerProfile.name` from DB once per worker instance.

**Modify**: `backend/app/services/connections.py` (line 225)

Same pattern — prefix the connection discovery system prompt with owner name context for better relationship identification.

**Modify**: `backend/app/routers/memories.py` (line 305)

Reflection prompt: prefix with owner name if available.

---

## Epic 6: Tests

### Task 6.1: Owner profile API tests

**New file**: `backend/tests/test_owner.py`

Test cases:
- `test_get_owner_profile_creates_default` — GET returns empty profile on first call
- `test_update_owner_profile` — PUT updates name, dob, bio
- `test_update_owner_profile_links_person` — PUT with person_id sets that Person's relationship_to_owner="self"
- `test_get_owner_family` — returns only persons with relationship_to_owner set, excludes "self"
- `test_family_excludes_unrelated_persons` — persons without relationship_to_owner are not in family list

### Task 6.2: Person model extension tests

**Modify**: `backend/tests/test_persons.py` (or create if doesn't exist)

Test cases:
- `test_create_person_with_relationship` — POST with relationship_to_owner
- `test_update_person_relationship` — PUT to set/change relationship
- `test_update_person_deceased` — PUT to mark deceased

### Task 6.3: Chat intelligence tests

**Modify**: `backend/tests/test_conversations.py`

- `test_persist_exchange_returns_needs_title` — verify the new return tuple
- `test_ai_title_generation` — mock `LLMService.generate()`, verify `title_update` WebSocket message sent and DB updated
- `test_system_prompt_includes_date` — verify today's date appears in system prompt
- `test_system_prompt_includes_owner_name` — verify owner name in system prompt when configured
- `test_system_prompt_without_owner` — verify graceful fallback when no owner configured

### Task 6.4: GEDCOM import tests

**New file**: `backend/tests/test_gedcom.py`

Test with a minimal GEDCOM fixture file:
- `test_import_creates_persons` — verify Person records created with names
- `test_import_deduplicates_by_gedcom_id` — re-import updates, doesn't duplicate
- `test_import_sets_relationships` — verify relationship_to_owner mapped correctly for spouse/child/parent/sibling
- `test_import_marks_deceased` — verify is_deceased set when death record present
- `test_import_invalid_file` — 422 for non-GEDCOM file

### Task 6.5: Owner context in RAG tests

**New file or extend**: `backend/tests/test_rag.py`

- `test_build_system_prompt_with_owner` — verify owner name and date in prompt
- `test_build_system_prompt_with_family` — verify family context block
- `test_build_system_prompt_without_owner` — verify generic fallback

---

## Files Summary

### New files
| File | Description |
|------|-------------|
| `backend/app/models/owner.py` | OwnerProfile singleton model + schemas |
| `backend/app/routers/owner.py` | Owner profile + family + GEDCOM endpoints |
| `backend/app/services/owner_context.py` | Build (owner_name, family_context) from DB |
| `backend/app/services/gedcom_import.py` | Parse GEDCOM, create Person records with relationships |
| `backend/tests/test_owner.py` | Owner API tests |
| `backend/tests/test_gedcom.py` | GEDCOM import tests |

### Modified files
| File | Changes |
|------|---------|
| `backend/app/models/person.py` | Add relationship_to_owner, is_deceased, gedcom_id columns + update schemas |
| `backend/app/models/__init__.py` | Register OwnerProfile import |
| `backend/app/db.py` | Add migration for 3 new Person columns |
| `backend/app/main.py` | Register owner router |
| `backend/app/routers/persons.py` | Handle new fields in create/update |
| `backend/app/routers/chat.py` | Wire owner context to RAGService, add _generate_title async function, modify _persist_exchange return |
| `backend/app/services/rag.py` | New system prompt template with date/owner/family, _build_system_prompt method, extended __init__ |
| `backend/app/worker.py` | Prefix enrichment prompts with owner name |
| `backend/app/services/connections.py` | Prefix connection discovery prompt with owner name |
| `backend/app/routers/memories.py` | Prefix reflection prompt with owner name |
| `backend/requirements.txt` | Add python-gedcom |
| `frontend/src/types/index.ts` | OwnerProfile types, Person relationship fields, title_update message type |
| `frontend/src/services/api.ts` | Owner profile + family + GEDCOM API functions |
| `frontend/src/components/Settings.tsx` | New Owner Identity section with family management + GEDCOM upload |
| `frontend/src/components/Chat.tsx` | Handle title_update WebSocket message |
| `frontend/src/components/People.tsx` | Show relationship badges and deceased indicator |
| `backend/tests/test_conversations.py` | Update for new _persist_exchange return + add title generation tests |

---

## Implementation Order

The epics should be implemented in this sequence (each epic is self-contained and testable):

1. **Epic 1** (Owner Profile Backend) — foundation for everything else
2. **Epic 2** (Chat Intelligence) — depends on Epic 1 for owner context
3. **Epic 4** (Frontend Settings UI) — depends on Epic 1 for API
4. **Epic 3** (GEDCOM Import) — depends on Epic 1 for model
5. **Epic 5** (Background AI enrichment) — depends on Epic 1 for owner context
6. **Epic 6** (Tests) — run after each epic, but final comprehensive pass at end

---

## Verification

1. **Backend API**: `curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/owner/profile` returns profile
2. **Owner update**: PUT owner name, verify Chat system prompt includes it (check via chat response mentioning the name)
3. **Date awareness**: Ask the chat "what day is it today?" — verify it knows the correct date
4. **Smart titles**: Start a new conversation, send a message. Verify sidebar title updates from truncated text to AI-generated title within a few seconds.
5. **Family in Settings**: Open Settings, verify Owner Identity section. Add a family member, verify it appears.
6. **Family in chat**: After adding family members, chat should reference them correctly (e.g., "your wife Ana" if "Ana (spouse)" is configured).
7. **GEDCOM import**: Upload a .ged file in Settings, verify persons created with correct relationships.
8. **People page**: Verify relationship badges appear on Person cards.
9. **Graceful degradation**: When no owner profile configured, everything works as before (generic system prompt, truncated titles).
