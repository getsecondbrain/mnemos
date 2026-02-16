# Mnemos — Feature Roadmap

Living document for planned features beyond the core implementation (IMPL_PLAN.md).
Phases are roughly ordered by dependency and complexity, not strict timelines.

---

## Phase 1: Memory Card Actions

Quick interactions on each timeline card via a three-dot context menu.

### Features
- **Edit** — open inline or modal editor for title, content, tags
- **Delete** — with confirmation dialog, cascades to vault/source cleanup
- **Visibility toggle** — mark memory as Private or Public

### Private Memories
- New `visibility` field on Memory model: `public` (default) | `private`
- Private memories hidden from default timeline view
- Accessible via explicit filter toggle ("Show private")
- Similar to Apple Photos hidden album — exists but stays out of the way

### Backend
- Add `visibility TEXT DEFAULT 'public'` column to `memories` table
- `PATCH /api/memories/:id` already exists — extend to accept `visibility`
- `GET /api/memories` filters `WHERE visibility = 'public'` unless `?include_private=true`
- `DELETE /api/memories/:id` — cascade delete source, vault file, embeddings, connections

### Frontend
- `MemoryCardMenu` component — three-dot button, dropdown with Edit/Delete/Private toggle
- Render on each timeline card (top-right, like Facebook's `...` button)
- Inline edit mode or navigate to `/memory/:id/edit`
- Confirmation modal for delete

---

## Phase 2: "On This Day" Carousel

Facebook-style memory highlights at the top of the Timeline feed.

### Features
- Horizontal scrollable carousel showing memories from today's date in past years
- Each card shows the memory title, a thumbnail (if photo), the year, and a prompt
- Interactive: LLM-generated prompts invite the user to engage
  - "Add more context to this memory"
  - "How do you feel about this now?"
  - "This was 3 years ago — has anything changed?"
- Dismissible per-session (don't nag)
- Empty state: hidden entirely if no past memories match today's date

### Backend
- `GET /api/memories/on-this-day` — returns memories where `month(captured_at) = :month AND day(captured_at) = :day AND year(captured_at) < :year`
- LLM prompt generation via existing Ollama integration — given memory content, generate a short engagement question
- Optional: cache generated prompts so they don't change on every page load

### Frontend
- `OnThisDay` component — horizontal scroll container with snap points
- Renders above the TimelineBar (or between TimelineBar and QuickCapture)
- Cards link to the full memory view
- "Respond" button opens QuickCapture pre-filled with context

---

## Phase 3: Sidebar Filters

Facebook-inspired left sidebar with timeline filter controls.

### Layout (Desktop)
```
+------------------+----------------------------------------+
| Mnemos (logo)    |                                        |
|------------------|            Timeline Feed               |
| Navigation       |                                        |
|   Capture        |  [On This Day carousel]                |
|   Search         |  [Timeline Bar]                        |
|   Chat           |  [QuickCapture]                        |
|   Graph          |  [Memory cards...]                     |
|   Heartbeat      |                                        |
|   Testament      |                                        |
|   Settings       |                                        |
|------------------|                                        |
| Filters          |                                        |
|   Date range     |                                        |
|   Content type   |                                        |
|   Tags           |                                        |
|   People         |                                        |
|   Location       |                                        |
|   Visibility     |                                        |
|------------------|                                        |
| Lock & Logout    |                                        |
+------------------+----------------------------------------+
```

### Filter Types
- **Date range** — beyond the year bar, allow month/day granularity
- **Content type** — text, photo, file, voice, URL (checkbox multi-select)
- **Tags** — select one or more tags to filter by
- **People** — filter by tagged faces (depends on Phase 5 or Immich integration)
- **Location** — filter by place (depends on geo-tagging)
- **Visibility** — show all / public only / private only

### Backend
- Extend `GET /api/memories` with query params: `content_type`, `tag_ids`, `person_ids`, `location`, `visibility`, `date_from`, `date_to`
- Compound filters with AND logic

### Frontend
- Collapsible filter panel in the sidebar (below nav, above logout)
- Active filters shown as removable chips above the timeline
- Mobile: filter panel as a slide-out sheet or modal

---

## Phase 4: Background AI Loops

Persistent worker loops that continuously evaluate, sort, and enrich content.

### Loop Types

| Loop | Cadence | Purpose |
|------|---------|---------|
| **Connection Discovery** | Every 6h | Find new semantic connections between memories |
| **Tag Suggestion** | On ingest + daily sweep | Suggest tags for untagged content |
| **Memory Enrichment** | Daily | Generate prompts for incomplete or thin memories |
| **Pattern Detection** | Weekly | Identify recurring themes, people, locations |
| **Digest Generation** | Weekly/Monthly | Summarize recent memories into a narrative |
| **Stale Review** | Monthly | Surface old memories that might need updating |

### Architecture
- Extends existing `worker.py` job system
- Each loop type is a `JobType` with its own schedule
- Loops are idempotent — safe to re-run, skip if already processed
- Results stored as system-generated memories or metadata annotations
- User can dismiss/accept suggestions from the UI

### Backend
- `LoopScheduler` class manages cadences and triggers jobs
- New `JobType` variants: `TAG_SUGGEST`, `ENRICH_PROMPT`, `PATTERN_DETECT`, `DIGEST`
- `GET /api/suggestions` — returns pending AI suggestions for user review
- `POST /api/suggestions/:id/accept` / `POST /api/suggestions/:id/dismiss`

### Frontend
- Suggestions surface as cards in the timeline (distinct styling from memory cards)
- Accept/dismiss buttons inline
- Settings page: toggle individual loop types on/off, adjust cadences

---

## Phase 5: Face Detection & Tagging

Detect, cluster, and identify faces in photos and videos.

### Build vs. Integrate Decision

**Recommendation: Integrate with Immich.**

| Approach | Effort | Quality | Maintenance |
|----------|--------|---------|-------------|
| Build in Mnemos | 3-6 months | Good (insightface/mediapipe) | High — ML model updates, edge cases |
| Integrate with Immich | 2-4 weeks | Excellent (mature, battle-tested) | Low — Immich team maintains ML pipeline |

Immich already has: face detection, face clustering, face recognition, geo-tagging,
mobile upload, video processing, and a REST API. Duplicating this is not a good use of time.

### Integration Path (Recommended)
- Immich handles photo storage, face detection, geo-tagging, albums
- Mnemos syncs face/person identities from Immich via API
- Mnemos uses those identities in its knowledge graph and timeline filters
- Two-way sync: tag a face in Immich, it appears in Mnemos; tag in Mnemos, push to Immich
- Mnemos adds what Immich lacks: encrypted storage, AI connections between photos and text/voice/URL memories, inheritance via Shamir SSS

### If Building Natively (Fallback)
- Face detection: `insightface` or `mediapipe` (Python, runs locally)
- Face clustering: group unknown faces by embedding similarity
- Tagging UI: "Untagged faces" queue — user names each cluster
- Auto-identify: once a face is tagged, match new detections against known embeddings
- New `Person` model linked to memories via `MemoryPerson` join table
- `GET /api/faces/untagged` — returns face crops awaiting identification
- `POST /api/faces/:id/tag` — assign a person to a face cluster

### Frontend (Either Path)
- "People" section in sidebar filters
- Person detail page: all memories containing that person
- Face tagging modal: shows cropped face, user types/selects name
- Badge on nav showing count of untagged faces

---

## Phase 6: Location & Geo-Tagging

### Features
- Extract GPS coordinates from photo EXIF data on ingest
- Manual location tagging for text/voice memories
- Reverse geocode coordinates to place names
- Map view of memories (Leaflet or Mapbox)
- "Memories from this place" grouping

### Backend
- `latitude`, `longitude`, `place_name` fields on Memory model
- EXIF extraction during photo ingest pipeline
- Reverse geocoding via Nominatim (self-hosted) or similar
- `GET /api/memories?near=lat,lng,radius_km`

### Frontend
- Map view as a new route (`/map`)
- Location filter in sidebar
- Location display on memory cards

---

## Open Questions

1. **Immich integration timeline** — Should this block Phase 5, or should we build a minimal native face detection first and migrate to Immich later?
2. **Privacy model granularity** — Is `public`/`private` enough, or do we need per-person sharing (e.g., share a memory with a specific heir)?
3. **AI loop resource budget** — How much local compute can we dedicate to background loops? This affects cadence and model choice.
4. **Mobile app** — Several of these features (especially photo capture, face tagging, location) are significantly better with a native mobile app. Is a React Native or PWA wrapper on the roadmap?
