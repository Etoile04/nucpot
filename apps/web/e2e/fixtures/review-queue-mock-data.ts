/**
 * Mock data fixtures for Review Queue E2E tests.
 *
 * Covers KG review queue items and conflict resolution items
 * for testing auth redirect, queue rendering, and batch actions.
 *
 * Spec: NFM-1400
 */

// ── Auth fixtures ──────────────────────────────────────────────────────────

export const MOCK_USER_PROFILE = {
  id: "user-mock-001",
  username: "reviewer",
  email: "reviewer@nucpot.test",
  full_name: "Test Reviewer",
  blog_role: "admin",
  is_active: true,
} as const

export const MOCK_AUTH_ME_RESPONSE = {
  success: true,
  data: MOCK_USER_PROFILE,
} as const

export const MOCK_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-review-token-nfm1400"

// ── KG Review Queue fixtures ───────────────────────────────────────────────

interface KgReviewItem {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly source: string
  readonly confidence: number
  readonly status: "pending" | "approved" | "rejected"
  readonly createdAt: string
}

export const MOCK_KG_REVIEW_ITEMS: ReadonlyArray<KgReviewItem> = [
  {
    id: "kg-item-001",
    title: "Uranium-235 半经验势",
    type: "potential",
    source: "NIST Interatomic Potentials Repository",
    confidence: 0.92,
    status: "pending",
    createdAt: "2026-07-14T08:30:00Z",
  },
  {
    id: "kg-item-002",
    title: "BCC铁 EAM势函数",
    type: "eam",
    source: "Molecular Dynamics Data Bank",
    confidence: 0.87,
    status: "pending",
    createdAt: "2026-07-14T07:15:00Z",
  },
  {
    id: "kg-item-003",
    title: "SiO2 Tersoff势",
    type: "tersoff",
    source: "OpenKIM Repository",
    confidence: 0.95,
    status: "pending",
    createdAt: "2026-07-14T06:00:00Z",
  },
]

export const MOCK_KG_REVIEW_PENDING_RESPONSE = {
  items: MOCK_KG_REVIEW_ITEMS,
  total: 3,
  page: 1,
  pageSize: 20,
} as const

export const MOCK_KG_REVIEW_AFTER_BATCH = {
  items: [MOCK_KG_REVIEW_ITEMS[2]],
  total: 1,
  page: 1,
  pageSize: 20,
} as const

export const MOCK_BATCH_APPROVE_RESPONSE = { updated: 2 } as const

export const MOCK_BATCH_REJECT_RESPONSE = { updated: 1 } as const

// ── Conflict Resolution fixtures ────────────────────────────────────────────

interface ConflictSource {
  readonly id: string
  readonly sourceTitle: string
  readonly value: string
  readonly unit: string
  readonly confidence: number
}

interface ConflictItem {
  readonly id: string
  readonly entityName: string
  readonly property: string
  readonly sourceA: ConflictSource
  readonly sourceB: ConflictSource
  readonly conflictNumber: number
}

export const MOCK_CONFLICT_ITEMS: ReadonlyArray<ConflictItem> = [
  {
    id: "conflict-001",
    entityName: "U-235",
    property: "lattice_constant",
    sourceA: {
      id: "src-a-001",
      sourceTitle: "NIST Database",
      value: "4.95",
      unit: "Å",
      confidence: 0.91,
    },
    sourceB: {
      id: "src-b-001",
      sourceTitle: "Published Paper (2024)",
      value: "4.87",
      unit: "Å",
      confidence: 0.88,
    },
    conflictNumber: 1,
  },
  {
    id: "conflict-002",
    entityName: "Fe-BCC",
    property: "cohesive_energy",
    sourceA: {
      id: "src-a-002",
      sourceTitle: "OpenKIM",
      value: "4.28",
      unit: "eV/atom",
      confidence: 0.94,
    },
    sourceB: {
      id: "src-b-002",
      sourceTitle: "DFT Calculation",
      value: "4.15",
      unit: "eV/atom",
      confidence: 0.85,
    },
    conflictNumber: 2,
  },
]

export const MOCK_CONFLICTS_RESPONSE = {
  items: MOCK_CONFLICT_ITEMS,
  total: 2,
  page: 1,
  pageSize: 20,
} as const

export const MOCK_RESOLVE_CONFLICT_RESPONSE = { resolved: true } as const
