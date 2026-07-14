/**
 * Mock fixtures for the Review Queue E2E flow (NFM-1402).
 *
 * Mirrors the frontend API contracts from:
 *   - `review-api.ts`   (ReviewItem, ReviewListResponse)
 *   - `kg-review-api.ts` (ConflictItem, ConflictSource)
 *   - `api-client.ts`   (UserProfile, TokenResponse)
 *
 * All timestamps are computed at module load for run stability.
 * All IDs use stable prefixes so locators never flake on randomness.
 */

// ── Shared Types ──────────────────────────────────────────────────────────

export interface MockUserProfile {
  readonly id: string
  readonly username: string
  readonly email: string
  readonly full_name: string | null
  readonly blog_role: string | null
  readonly is_active: boolean
}

export interface MockReviewItem {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly source: string
  readonly confidence: number
  readonly status: "pending" | "approved" | "rejected"
  readonly createdAt: string
}

export interface MockConflictSource {
  readonly id: string
  readonly sourceTitle: string
  readonly value: string
  readonly unit: string
  readonly confidence: number
}

export interface MockConflictItem {
  readonly id: string
  readonly entityName: string
  readonly property: string
  readonly sourceA: MockConflictSource
  readonly sourceB: MockConflictSource
  readonly conflictNumber: number
}

// ── Timestamps ────────────────────────────────────────────────────────────

const NOW = new Date().toISOString()

// ── Auth fixtures ──────────────────────────────────────────────────────────

export const MOCK_USER_PROFILE: MockUserProfile = {
  id: "usr-mock-001",
  username: "test_reviewer",
  email: "reviewer@nucpot.test",
  full_name: "Test Reviewer",
  blog_role: "reviewer",
  is_active: true,
}

export const MOCK_AUTH_ME_RESPONSE = {
  success: true,
  data: MOCK_USER_PROFILE,
}

export const MOCK_LOGIN_RESPONSE = {
  success: true,
  data: {
    access_token: "mock-jwt-reviewer-001",
    token_type: "bearer",
  },
}

// ── KG Review queue fixtures ──────────────────────────────────────────────

export const MOCK_KG_REVIEW_ITEMS: readonly MockReviewItem[] = [
  {
    id: "kg-ent-001",
    title: "UO2 晶体结构",
    type: "entity",
    source: "UO2 晶体结构分析报告",
    confidence: 0.92,
    status: "pending",
    createdAt: NOW,
  },
  {
    id: "kg-prop-001",
    title: "Zr-4 合金腐蚀数据",
    type: "property",
    source: "Zr-4 合金在高温水中的腐蚀行为研究",
    confidence: 0.85,
    status: "pending",
    createdAt: NOW,
  },
  {
    id: "kg-rel-001",
    title: "BeO 热导率",
    type: "relation",
    source: "BeO 陶瓷材料热物性参数手册",
    confidence: 0.78,
    status: "pending",
    createdAt: NOW,
  },
]

export const MOCK_KG_REVIEW_RESPONSE = {
  items: MOCK_KG_REVIEW_ITEMS,
  total: 3,
  page: 1,
  pageSize: 20,
}

export const MOCK_KG_BATCH_RESPONSE = {
  updated: 2,
}

/** Stats responses — one per status, each with total count only. */
export const MOCK_KG_STATS_PENDING = {
  items: [],
  total: 3,
  page: 1,
  pageSize: 1,
}

export const MOCK_KG_STATS_APPROVED = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 1,
}

export const MOCK_KG_STATS_REJECTED = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 1,
}

// ── Conflict queue fixtures ────────────────────────────────────────────────

export const MOCK_CONFLICT_ITEMS: readonly MockConflictItem[] = [
  {
    id: "conf-001",
    entityName: "UO2",
    property: "密度",
    sourceA: {
      id: "src-a-001",
      sourceTitle: "UO2 物性数据集 v2",
      value: "10.97",
      unit: "g/cm³",
      confidence: 0.91,
    },
    sourceB: {
      id: "src-b-001",
      sourceTitle: "核燃料材料手册",
      value: "10.96",
      unit: "g/cm³",
      confidence: 0.88,
    },
    conflictNumber: 1,
  },
  {
    id: "conf-002",
    entityName: "Zr-4",
    property: "热中子吸收截面",
    sourceA: {
      id: "src-a-002",
      sourceTitle: "锆合金核性能数据库",
      value: "0.18",
      unit: "barn",
      confidence: 0.85,
    },
    sourceB: {
      id: "src-b-002",
      sourceTitle: "反应堆物理设计原理",
      value: "0.22",
      unit: "barn",
      confidence: 0.79,
    },
    conflictNumber: 2,
  },
]

export const MOCK_CONFLICTS_RESPONSE = {
  items: MOCK_CONFLICT_ITEMS,
  total: 2,
  page: 1,
  pageSize: 20,
}

export const MOCK_RESOLVE_RESPONSE = {
  resolved: true,
}
