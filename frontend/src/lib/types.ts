// ── Auth Types ────────────────────────────────────────

export interface User {
  user_id: string
  email: string
  name: string
  role: 'admin' | 'editor' | 'viewer'
  org_id: string
  org_name: string
  is_superadmin?: boolean
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface InviteVerifyResponse {
  valid: boolean
  org_name: string | null
  role: string | null
  message: string
}

// ── Chat Types ────────────────────────────────────────

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  usage?: TokenUsage
  query_type?: string
  cached?: boolean
  created_at: string
  streaming?: boolean
  status_messages?: string[]
}

export interface Source {
  filename: string
  page_number: number | null
  section_heading: string | null
  chunk_text_preview: string
  relevance_score: number
}

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated_cost_usd: number
}

export interface ChatResponse {
  response_id: string
  content: string
  query_type: string
  sources: Source[]
  usage: TokenUsage
  eval_scores: { faithfulness: number | null; relevancy: number | null } | null
  cached: boolean
  created_at: string
}

// ── SSE Event Types ───────────────────────────────────

export interface StreamEvent {
  type: 'stream_start' | 'stream_end' | 'content_delta' | 'section_start' |
        'section_end' | 'status_update' | 'error'
  content?: string
  heading?: string
  message?: string
  response_id?: string
  query_type?: string
  sources?: Source[]
  usage?: TokenUsage
  error_detail?: string
}

// ── Document Types ────────────────────────────────────

export interface Document {
  document_id: string
  filename: string
  content_type: string
  status: 'pending' | 'processing' | 'indexed' | 'failed'
  error_message: string | null
  chunk_count: number | null
  version: number
  metadata: Record<string, string>
  created_at: string
  updated_at: string
}

export interface DocumentListResponse {
  documents: Document[]
  total: number
  page: number
  page_size: number
}

// ── Admin Types ───────────────────────────────────────

export interface SystemStats {
  organization: { org_id: string }
  documents: {
    by_status: Record<string, number>
    total_indexed: number
    total_chunks: number
  }
  jobs: { by_status: Record<string, number> }
  vector_store: Record<string, any>
  cache: {
    keys?: number
    org_keys?: number
    memory_used: string
  }
}

export interface InviteCode {
  code: string
  role: string
  max_uses: number
  times_used: number
  is_active: boolean
  expires_at: string
  created_at: string
}

export interface OrgUser {
  user_id: string
  email: string
  name: string
  role: string
  is_active: boolean
  created_at: string
}

export interface AuditEntry {
  action: string
  user_id?: string
  resource_type?: string
  resource_id?: string
  detail?: Record<string, any>
  ip_address?: string
  created_at: string
}