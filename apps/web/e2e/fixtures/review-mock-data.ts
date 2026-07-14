/**
 * Mock data fixtures for the Review Queue E2E auth flow tests.
 *
 * Scope: NFM-1400 (Review Queue auth flow). Covers the auth-protected
 * review pages (/review/kg, /review/conflicts) and their underlying
 * /api/v1/auth/* and /api/v1/review/* endpoints.
 *
 * These fixtures are intentionally minimal and shape-locked to the
 * TypeScript types in apps/web/src/lib/api-client.ts and
 * apps/web/src/lib/review-api.ts. If those types change, update
 * this file in the same commit.
 */

// ── Auth fixtures ──────────────────────────────────────────────────────

export const MOCK_AUTH_TOKEN = "mock-review-jwt-token-payload"
export const MOCK_AUTH_STORAGE_KEY = "blog_admin_token"

export const MOCK_USER_PROFILE = {
  id: "user-review-001",
  username: "reviewer",
  email: "reviewer@example.com",
  full_name: "Reviewer One",
  blog_role: "reviewer",
  is_active: true,
}

export const MOCK_TOKEN_RESPONSE = {
  access_token: MOCK_AUTH_TOKEN,
  token_type: "bearer",
}

// ── KG Review Queue fixtures ───────────────────────────────────────────

export const MOCK_KG_REVIEW_ITEMS = [
  {
    id: "kg-review-001",
    title: "UO2 密度属性",
    type: "实体",
    source: "文献 A",
    confidence: 0.92,
    status: "pending" as const,
    createdAt: "2025-01-15T00:00:00Z",
  },
  {
    id: "kg-review-002",
    title: "UO2 熔点属性",
    type: "属性",
    source: "文献 B",
    confidence: 0.85,
    status: "pending" as const,
    createdAt: "2025-01-16T00:00:00Z",
  },
  {
    id: "kg-review-003",
    title: "ZrC 杨氏模量",
    type: "属性",
    source: "文献 C",
    confidence: 0.78,
    status: "pending" as const,
    createdAt: "2025-01-17T00:00:00Z",
  },
] as const

export const MOCK_KG_REVIEW_QUEUE_RESPONSE = {
  items: [...MOCK_KG_REVIEW_ITEMS],
  total: 3,
  page: 1,
  pageSize: 20,
}

export const MOCK_KG_REVIEW_QUEUE_EMPTY_RESPONSE = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 20,
}

// ── Conflict Queue fixtures ────────────────────────────────────────────

export const MOCK_CONFLICT_ITEMS = [
  {
    id: "conflict-001",
    entityName: "UO2",
    property: "密度",
    sourceA: {
      id: "src-a-001",
      sourceTitle: "文献 A",
      value: "10.96",
      unit: "g/cm³",
      confidence: 0.9,
    },
    sourceB: {
      id: "src-b-001",
      sourceTitle: "文献 B",
      value: "10.97",
      unit: "g/cm³",
      confidence: 0.85,
    },
    conflictNumber: 1,
  },
  {
    id: "conflict-002",
    entityName: "ZrC",
    property: "熔点",
    sourceA: {
      id: "src-a-002",
      sourceTitle: "文献 C",
      value: "3540",
      unit: "K",
      confidence: 0.88,
    },
    sourceB: {
      id: "src-b-002",
      sourceTitle: "文献 D",
      value: "3545",
      unit: "K",
      confidence: 0.83,
    },
    conflictNumber: 2,
  },
] as const

export const MOCK_CONFLICT_QUEUE_RESPONSE = {
  items: [...MOCK_CONFLICT_ITEMS],
  total: 2,
  page: 1,
  pageSize: 20,
}

// ── Batch action response ─────────────────────────────────────────────

export const MOCK_KG_BATCH_RESPONSE = {
  updated: MOCK_KG_REVIEW_ITEMS.length,
}