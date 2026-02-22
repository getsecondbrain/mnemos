import type {
  Memory,
  MemoryCreate,
  MemoryUpdate,
  SaltResponse,
  TokenResponse,
  IngestResponse,
  SearchResponse,
  Connection,
  HeartbeatStatus,
  TestamentConfig,
  ShamirSplitResponse,
  Heir,
  AuditLogEntry,
  Tag,
  TagCreate,
  TagUpdate,
  MemoryTag,
  Suggestion,
  LoopSetting,
  Person,
  PersonDetail,
  PersonCreate,
  PersonUpdate,
  MemoryPersonLink,
  LinkPersonRequest,
  OwnerProfile,
  OwnerProfileUpdate,
  GedcomImportResult,
} from "../types";

const BASE_URL = "/api";

// --- Auth token provider ----------------------------------------------------

let getAccessTokenFn: (() => string | null) | null = null;

export function setAuthTokenProvider(fn: () => string | null): void {
  getAccessTokenFn = fn;
}

// --- Core request helper ----------------------------------------------------

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> | undefined),
  };

  // Inject Bearer token when available
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail ?? res.statusText);
  }

  // 204 No Content (e.g., DELETE)
  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// --- Auth endpoints ---------------------------------------------------------

export async function getSalt(): Promise<SaltResponse> {
  return request<SaltResponse>("/auth/salt");
}

export async function postSetup(body: {
  hmac_verifier: string;
  argon2_salt: string;
  master_key_b64: string;
}): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/setup", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function postLogin(body: {
  hmac_verifier: string;
  master_key_b64: string;
}): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function postLogout(accessToken: string): Promise<void> {
  return request<void>("/auth/logout", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function postRefresh(body: {
  refresh_token: string;
}): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getAuthStatus(accessToken: string): Promise<{
  authenticated: boolean;
  session_id: string;
  encryption_ready: boolean;
}> {
  return request("/auth/status", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

// --- Memory endpoints -------------------------------------------------------

export async function createMemory(body: MemoryCreate): Promise<Memory> {
  return request<Memory>("/memories", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listMemories(params?: {
  skip?: number;
  limit?: number;
  content_type?: string;
  tag_ids?: string[];
  person_ids?: string[];
  year?: number;
  order_by?: string;
  visibility?: string;  // "public" | "private" | "all"
  date_from?: string;  // ISO date string, e.g. "2024-01-01"
  date_to?: string;    // ISO date string, e.g. "2024-12-31"
  near?: string;       // "lat,lng,radius_km" e.g. "48.8566,2.3522,10"
  has_location?: boolean;
}): Promise<Memory[]> {
  const query = new URLSearchParams();
  if (params?.skip != null) query.set("skip", String(params.skip));
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.content_type) query.set("content_type", params.content_type);
  if (params?.tag_ids) {
    for (const tid of params.tag_ids) {
      query.append("tag_ids", tid);
    }
  }
  if (params?.person_ids) {
    for (const pid of params.person_ids) {
      query.append("person_ids", pid);
    }
  }
  if (params?.year != null) query.set("year", String(params.year));
  if (params?.order_by) query.set("order_by", params.order_by);
  if (params?.visibility) query.set("visibility", params.visibility);
  if (params?.date_from) query.set("date_from", params.date_from);
  if (params?.date_to) query.set("date_to", params.date_to);
  if (params?.near) query.set("near", params.near);
  if (params?.has_location != null) query.set("has_location", String(params.has_location));
  const qs = query.toString();
  return request<Memory[]>(`/memories${qs ? `?${qs}` : ""}`);
}

export interface TimelineYearStat {
  year: number;
  count: number;
}

export interface TimelineStats {
  years: TimelineYearStat[];
  total: number;
  earliest_year: number | null;
  latest_year: number | null;
}

export async function getTimelineStats(params?: {
  visibility?: string;  // "public" | "private" | "all"
}): Promise<TimelineStats> {
  const query = new URLSearchParams();
  if (params?.visibility) query.set("visibility", params.visibility);
  const qs = query.toString();
  return request<TimelineStats>(`/memories/stats/timeline${qs ? `?${qs}` : ""}`);
}

export async function getMemory(id: string): Promise<Memory> {
  return request<Memory>(`/memories/${id}`);
}

export async function updateMemory(
  id: string,
  body: MemoryUpdate,
): Promise<Memory> {
  return request<Memory>(`/memories/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteMemory(id: string): Promise<{ id: string }> {
  return request<{ id: string }>(`/memories/${id}`, {
    method: "DELETE",
  });
}

export async function undeleteMemory(id: string): Promise<Memory> {
  return request<Memory>(`/memories/${id}/undelete`, {
    method: "POST",
  });
}

// --- On This Day endpoints ---------------------------------------------------

export async function getOnThisDayMemories(params?: {
  visibility?: string;
}): Promise<Memory[]> {
  const query = new URLSearchParams();
  if (params?.visibility) query.set("visibility", params.visibility);
  const qs = query.toString();
  return request<Memory[]>(`/memories/on-this-day${qs ? `?${qs}` : ""}`);
}

export async function getMemoryReflect(memoryId: string): Promise<{ prompt: string }> {
  return request<{ prompt: string }>(`/memories/${memoryId}/reflect`);
}

export async function reprocessExif(memoryId: string): Promise<Memory> {
  return request<Memory>(`/memories/${memoryId}/reprocess-exif`, {
    method: "POST",
  });
}

// --- Ingest endpoints -------------------------------------------------------

/**
 * Upload a file to the ingest endpoint with progress tracking.
 * Uses XMLHttpRequest instead of fetch for upload progress events.
 */
export function uploadFileWithProgress(
  file: File,
  capturedAt?: string,
  onProgress?: (percent: number) => void,
  parentId?: string,
): Promise<IngestResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE_URL}/ingest/file`);

    // Auth header
    const token = getAccessTokenFn?.();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }

    // Progress tracking
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        try {
          const detail = JSON.parse(xhr.responseText);
          reject(new ApiError(xhr.status, detail.detail ?? xhr.statusText));
        } catch {
          reject(new ApiError(xhr.status, xhr.statusText));
        }
      }
    });

    xhr.addEventListener("error", () => {
      reject(new ApiError(0, "Network error"));
    });

    const formData = new FormData();
    formData.append("file", file);
    if (capturedAt) {
      formData.append("captured_at", capturedAt);
    }
    if (parentId) {
      formData.append("parent_id", parentId);
    }

    xhr.send(formData);
  });
}

/**
 * Import content from a URL.
 * NOTE: Backend not yet implemented — will return 501.
 */
export async function ingestUrl(url: string, capturedAt?: string): Promise<IngestResponse> {
  return request<IngestResponse>("/ingest/url", {
    method: "POST",
    body: JSON.stringify({ url, captured_at: capturedAt }),
  });
}

// --- Search endpoints -------------------------------------------------------

export async function searchMemories(params: {
  q: string;
  mode?: "hybrid" | "keyword" | "semantic";
  top_k?: number;
  content_type?: string;
  tag_ids?: string[];
  person_ids?: string[];
}): Promise<SearchResponse> {
  const query = new URLSearchParams();
  query.set("q", params.q);
  if (params.mode) query.set("mode", params.mode);
  if (params.top_k != null) query.set("top_k", String(params.top_k));
  if (params.content_type) query.set("content_type", params.content_type);
  if (params.tag_ids) {
    for (const tid of params.tag_ids) {
      query.append("tag_ids", tid);
    }
  }
  if (params.person_ids) {
    for (const pid of params.person_ids) {
      query.append("person_ids", pid);
    }
  }
  return request<SearchResponse>(`/search?${query.toString()}`);
}

// --- Cortex endpoints -------------------------------------------------------

export async function getConnections(memoryId: string): Promise<Connection[]> {
  return request<Connection[]>(`/cortex/connections/${memoryId}`);
}

export async function getAllConnections(): Promise<Connection[]> {
  const memories = await listMemories({ limit: 200, visibility: "all" });
  const connectionSets = await Promise.all(
    memories.map((m) => getConnections(m.id).catch(() => [] as Connection[]))
  );
  const seen = new Set<string>();
  const all: Connection[] = [];
  for (const set of connectionSets) {
    for (const conn of set) {
      if (!seen.has(conn.id)) {
        seen.add(conn.id);
        all.push(conn);
      }
    }
  }
  return all;
}

// --- Heartbeat endpoints ----------------------------------------------------

export async function getHeartbeatChallenge(): Promise<{
  challenge: string;
  expires_at: string;
}> {
  return request("/heartbeat/challenge");
}

export async function postHeartbeatCheckin(body: {
  challenge: string;
  response_hmac: string;
}): Promise<{
  success: boolean;
  next_due: string;
  message: string;
}> {
  return request("/heartbeat/checkin", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getHeartbeatStatus(): Promise<HeartbeatStatus> {
  return request<HeartbeatStatus>("/heartbeat/status");
}

// --- Testament endpoints ----------------------------------------------------

export async function getTestamentConfig(): Promise<TestamentConfig> {
  return request<TestamentConfig>("/testament/config");
}

export async function updateTestamentConfig(body: {
  threshold?: number;
  total_shares?: number;
}): Promise<TestamentConfig> {
  return request<TestamentConfig>("/testament/config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function postShamirSplit(body: {
  passphrase?: string;
}): Promise<ShamirSplitResponse> {
  return request<ShamirSplitResponse>("/testament/shamir/split", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listHeirs(): Promise<Heir[]> {
  return request<Heir[]>("/testament/heirs");
}

export async function createHeir(body: {
  name: string;
  email: string;
  share_index?: number | null;
  role?: string;
}): Promise<Heir> {
  return request<Heir>("/testament/heirs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateHeir(
  id: string,
  body: {
    name?: string;
    email?: string;
    share_index?: number | null;
    role?: string;
  },
): Promise<Heir> {
  return request<Heir>(`/testament/heirs/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteHeir(id: string): Promise<void> {
  return request<void>(`/testament/heirs/${id}`, {
    method: "DELETE",
  });
}

export async function getTestamentAuditLog(): Promise<AuditLogEntry[]> {
  return request<AuditLogEntry[]>("/testament/audit-log");
}

// --- Tag endpoints -----------------------------------------------------------

export async function listTags(q?: string): Promise<Tag[]> {
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  const qs = query.toString();
  return request<Tag[]>(`/tags${qs ? `?${qs}` : ""}`);
}

export async function createTag(body: TagCreate): Promise<Tag> {
  return request<Tag>("/tags", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateTag(id: string, body: TagUpdate): Promise<Tag> {
  return request<Tag>(`/tags/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteTag(id: string): Promise<void> {
  return request<void>(`/tags/${id}`, {
    method: "DELETE",
  });
}

export async function getMemoryTags(memoryId: string): Promise<MemoryTag[]> {
  return request<MemoryTag[]>(`/memories/${memoryId}/tags`);
}

export async function addTagsToMemory(memoryId: string, tagIds: string[]): Promise<MemoryTag[]> {
  return request<MemoryTag[]>(`/memories/${memoryId}/tags`, {
    method: "POST",
    body: JSON.stringify({ tag_ids: tagIds }),
  });
}

export async function removeTagFromMemory(memoryId: string, tagId: string): Promise<void> {
  return request<void>(`/memories/${memoryId}/tags/${tagId}`, {
    method: "DELETE",
  });
}

// --- Vault endpoints ---------------------------------------------------------

export async function fetchVaultFile(sourceId: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE_URL}/vault/${sourceId}`, { headers });
  if (!res.ok) {
    throw new ApiError(res.status, "Failed to fetch vault file");
  }
  return res.blob();
}

export interface SourceMeta {
  source_id: string;
  mime_type: string;
  preservation_format: string;
  content_type: string;
  has_preserved_copy: boolean;
  original_size: number;
}

export async function fetchSourceMeta(sourceId: string): Promise<SourceMeta> {
  return request<SourceMeta>(`/vault/${sourceId}/meta`);
}

export async function fetchPreservedVaultFile(sourceId: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE_URL}/vault/${sourceId}/preserved`, { headers });
  if (!res.ok) {
    throw new ApiError(res.status, "Failed to fetch preserved vault file");
  }
  return res.blob();
}

// --- Admin endpoints --------------------------------------------------------

export interface ReprocessDetail {
  source_id: string;
  memory_id: string;
  mime_type: string;
  status: string;
  text_length: number | null;
  error: string | null;
}

export interface ReprocessResult {
  total_found: number;
  reprocessed: number;
  failed: number;
  skipped: number;
  details: ReprocessDetail[];
}

export async function reprocessSources(): Promise<ReprocessResult> {
  return request<ReprocessResult>("/admin/reprocess-sources", {
    method: "POST",
  });
}

// --- Export endpoints --------------------------------------------------------

export async function exportAllData(): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE_URL}/export`, {
    method: "POST",
    headers,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Export failed");
    throw new ApiError(res.status, text);
  }
  return res.blob();
}

// --- Suggestion endpoints ---------------------------------------------------

export async function getSuggestions(params?: {
  skip?: number;
  limit?: number;
}): Promise<Suggestion[]> {
  const query = new URLSearchParams();
  if (params?.skip != null) query.set("skip", String(params.skip));
  if (params?.limit != null) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request<Suggestion[]>(`/suggestions${qs ? `?${qs}` : ""}`);
}

export async function acceptSuggestion(id: string): Promise<Suggestion> {
  return request<Suggestion>(`/suggestions/${id}/accept`, {
    method: "POST",
  });
}

export async function dismissSuggestion(id: string): Promise<Suggestion> {
  return request<Suggestion>(`/suggestions/${id}/dismiss`, {
    method: "POST",
  });
}

// --- Loop settings endpoints ------------------------------------------------

export async function getLoopSettings(): Promise<LoopSetting[]> {
  return request<LoopSetting[]>("/settings/loops");
}

export async function updateLoopSetting(
  loopName: string,
  update: { enabled: boolean },
): Promise<LoopSetting> {
  return request<LoopSetting>(`/settings/loops/${loopName}`, {
    method: "PUT",
    body: JSON.stringify(update),
  });
}

// --- Person endpoints -------------------------------------------------------

export async function listPersons(params?: {
  skip?: number;
  limit?: number;
  q?: string;
}): Promise<Person[]> {
  const query = new URLSearchParams();
  if (params?.skip != null) query.set("skip", String(params.skip));
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.q) query.set("q", params.q);
  const qs = query.toString();
  return request<Person[]>(`/persons${qs ? `?${qs}` : ""}`);
}

export async function getPerson(id: string): Promise<PersonDetail> {
  return request<PersonDetail>(`/persons/${id}`);
}

export async function createPerson(body: PersonCreate): Promise<Person> {
  return request<Person>("/persons", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updatePerson(id: string, body: PersonUpdate): Promise<Person> {
  return request<Person>(`/persons/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deletePerson(id: string): Promise<void> {
  return request<void>(`/persons/${id}`, {
    method: "DELETE",
  });
}

export function getPersonThumbnailUrl(personId: string): string {
  return `/api/persons/${encodeURIComponent(personId)}/thumbnail`;
}

export async function fetchPersonThumbnail(personId: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const resp = await fetch(`${BASE_URL}/persons/${encodeURIComponent(personId)}/thumbnail`, {
    headers,
  });
  if (!resp.ok) throw new ApiError(resp.status, `Failed to fetch thumbnail: ${resp.status}`);
  return resp.blob();
}

export async function linkPersonToMemory(
  memoryId: string,
  body: LinkPersonRequest,
): Promise<MemoryPersonLink> {
  return request<MemoryPersonLink>(`/memories/${memoryId}/persons`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function unlinkPersonFromMemory(
  memoryId: string,
  personId: string,
): Promise<void> {
  return request<void>(`/memories/${memoryId}/persons/${personId}`, {
    method: "DELETE",
  });
}

export async function listMemoryPersons(memoryId: string): Promise<MemoryPersonLink[]> {
  return request<MemoryPersonLink[]>(`/memories/${memoryId}/persons`);
}

export async function triggerImmichSync(): Promise<{ status: string }> {
  return request<{ status: string }>("/persons/sync-immich", {
    method: "POST",
  });
}

export async function pushNameToImmich(personId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/persons/${personId}/push-name-to-immich`, {
    method: "POST",
  });
}

// --- Owner endpoints --------------------------------------------------------

export async function getOwnerProfile(): Promise<OwnerProfile> {
  return request<OwnerProfile>("/owner/profile");
}

export async function updateOwnerProfile(body: OwnerProfileUpdate): Promise<OwnerProfile> {
  return request<OwnerProfile>("/owner/profile", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function getOwnerFamily(): Promise<Person[]> {
  return request<Person[]>("/owner/family");
}

export async function importGedcom(
  file: File,
  ownerGedcomId?: string,
): Promise<GedcomImportResult> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const formData = new FormData();
  formData.append("file", file);

  const query = new URLSearchParams();
  if (ownerGedcomId) query.set("owner_gedcom_id", ownerGedcomId);
  const qs = query.toString();

  const res = await fetch(`${BASE_URL}/owner/gedcom${qs ? `?${qs}` : ""}`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail ?? res.statusText);
  }

  return res.json() as Promise<GedcomImportResult>;
}

// --- Immich endpoints --------------------------------------------------------

export interface ImmichOnThisDayAsset {
  asset_id: string;
  file_created_at: string;
  original_file_name: string;
  description: string | null;
  city: string | null;
  years_ago: number;
}

export async function getImmichOnThisDay(): Promise<ImmichOnThisDayAsset[]> {
  return request<ImmichOnThisDayAsset[]>("/immich/on-this-day");
}

export async function fetchImmichThumbnail(assetId: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(
    `${BASE_URL}/immich/assets/${encodeURIComponent(assetId)}/thumbnail`,
    { headers },
  );
  if (!res.ok) {
    throw new ApiError(res.status, "Failed to fetch Immich thumbnail");
  }
  return res.blob();
}

export async function fetchImmichOriginal(assetId: string): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {};
  const token = getAccessTokenFn?.();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(
    `${BASE_URL}/immich/assets/${encodeURIComponent(assetId)}/original`,
    { headers },
  );
  if (!res.ok) {
    throw new ApiError(res.status, "Failed to fetch Immich original");
  }
  const cd = res.headers.get("Content-Disposition") ?? "";
  let filename = `${assetId}.jpg`;
  const match = cd.match(/filename="?([^"]+)"?/);
  if (match?.[1]) filename = match[1];
  const blob = await res.blob();
  return { blob, filename };
}

// --- Geocoding (proxied through backend for Nominatim ToS compliance) -------

export interface GeocodingSearchResult {
  display_name: string;
  lat: string;
  lon: string;
}

export async function geocodingSearch(
  q: string,
  limit: number = 5,
): Promise<GeocodingSearchResult[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request<GeocodingSearchResult[]>(`/geocoding/search?${params}`);
}

export async function geocodingReverse(
  lat: number,
  lng: number,
): Promise<{ display_name: string }> {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  return request<{ display_name: string }>(`/geocoding/reverse?${params}`);
}

// --- Health -----------------------------------------------------------------

export async function healthCheck(): Promise<{
  status: string;
  service: string;
  version: string;
  checks: { database: string };
}> {
  return request("/health");
}
