/** Matches backend MemoryRead schema */
export interface Memory {
  id: string;
  created_at: string;
  updated_at: string;
  captured_at: string;
  title: string;
  content: string;
  content_type: string;
  source_type: string;
  metadata_json: string | null;
  content_hash: string | null;
  parent_id: string | null;
  source_id: string | null;
  title_dek: string | null;
  content_dek: string | null;
  encryption_algo: string | null;
  encryption_version: number | null;
}

/** Matches backend MemoryCreate schema */
export interface MemoryCreate {
  title: string;
  content: string;
  content_type?: string;
  source_type?: string;
  captured_at?: string;
  metadata_json?: string;
  parent_id?: string;
  source_id?: string;
  title_dek?: string;
  content_dek?: string;
  encryption_algo?: string;
  encryption_version?: number;
}

/** Matches backend MemoryUpdate schema */
export interface MemoryUpdate {
  title?: string;
  content?: string;
  content_type?: string;
  source_type?: string;
  captured_at?: string;
  metadata_json?: string;
  parent_id?: string;
  source_id?: string;
  title_dek?: string;
  content_dek?: string;
  encryption_algo?: string;
  encryption_version?: number;
}

/** Envelope encryption result from ClientCrypto */
export interface EncryptedEnvelope {
  ciphertext: Uint8Array;
  encryptedDek: Uint8Array;
  algo: string;
  version: number;
}

/** Auth salt response from backend */
export interface SaltResponse {
  salt: string;
  setup_required: boolean;
}

/** JWT token pair from backend */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

/** Response from POST /api/ingest/file or /api/ingest/text */
export interface IngestResponse {
  memory_id: string;
  source_id: string;
  content_type: string;
  mime_type: string;
  preservation_format: string;
}

/** Matches backend SearchHit schema from GET /api/search */
export interface SearchHit {
  memory_id: string;
  score: number;
  keyword_score: number | null;
  vector_score: number | null;
  matched_tokens: number | null;
}

/** Search response from GET /api/search */
export interface SearchResponse {
  hits: SearchHit[];
  total: number;
  query_tokens_generated: number;
  mode: string;
}

/** Matches backend ConnectionRead schema */
export interface Connection {
  id: string;
  source_memory_id: string;
  target_memory_id: string;
  created_at: string;
  relationship_type: string;
  strength: number;
  explanation_encrypted: string;
  explanation_dek: string;
  encryption_algo: string;
  encryption_version: number;
  generated_by: string;
  is_primary: boolean;
}

/** WebSocket message types for chat */
export type ChatMessageType = "auth" | "question" | "token" | "sources" | "done" | "error";

/** A single chat message in the UI */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: string[];       // memory IDs cited
  isStreaming?: boolean;     // true while tokens are arriving
}

/** Heartbeat alert record from backend */
export interface HeartbeatAlert {
  id: string;
  sent_at: string;
  alert_type: string;
  days_since_checkin: number;
  recipient: string;
  delivered: boolean;
  error_message: string | null;
}

/** Heartbeat status from GET /api/heartbeat/status */
export interface HeartbeatStatus {
  last_checkin: string | null;
  days_since: number | null;
  next_due: string | null;
  is_overdue: boolean;
  current_alert_level: string | null;
  alerts: HeartbeatAlert[];
}

/** Testament configuration from GET /api/testament/config */
export interface TestamentConfig {
  threshold: number;
  total_shares: number;
  shares_generated: boolean;
  generated_at: string | null;
  heir_mode_active: boolean;
  heir_mode_activated_at: string | null;
}

/** Shamir split response from POST /api/testament/shamir/split */
export interface ShamirSplitResponse {
  shares: string[];
  threshold: number;
  total_shares: number;
}

/** Heir record from GET /api/testament/heirs */
export interface Heir {
  id: string;
  name: string;
  email: string;
  share_index: number | null;
  role: string;
  created_at: string;
  updated_at: string;
}

/** Heir audit log entry from GET /api/testament/audit-log */
export interface AuditLogEntry {
  id: string;
  action: string;
  detail: string | null;
  ip_address: string | null;
  timestamp: string;
}

/** Matches backend TagRead schema */
export interface Tag {
  id: string;
  name: string;
  color: string | null;
  created_at: string;
  updated_at: string;
}

/** Matches backend TagCreate schema */
export interface TagCreate {
  name: string;
  color?: string;
}

/** Matches backend TagUpdate schema */
export interface TagUpdate {
  name?: string;
  color?: string;
}

/** Matches backend MemoryTagRead schema */
export interface MemoryTag {
  tag_id: string;
  tag_name: string;
  tag_color: string | null;
  created_at: string;
}
