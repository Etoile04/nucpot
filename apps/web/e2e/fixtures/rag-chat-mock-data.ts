/**
 * Mock fixtures for the RAG chat E2E flow (NFM-1399).
 *
 * The frontend `ragApi.query` reads the response JSON and then accesses
 *   - `response.answer`            (string)
 *   - `response.citations`         (RagCitation[])
 *   - `response.conversationId`    (string)
 * so the fixture below mirrors that shape so the mocked response satisfies
 * the frontend as if the backend produced it. This is intentional: E2E
 * tests validate the *frontend* rendering contract, not the API wire
 * envelope.
 *
 * Determinism notes
 *   - All timestamps are computed at module load so they are stable
 *     across runs in a single test invocation but still look realistic
 *     relative to a fresh `test.beforeEach`.
 *   - Citations use stable IDs so locators can target them with
 *     `data-testid="citation-${id}"` and never flake on randomness.
 */

export interface MockRagCitation {
  readonly id: string
  readonly source: string
  readonly excerpt: string
  readonly confidence: number
  readonly url?: string
}

export interface MockRagQueryResponse {
  readonly answer: string
  readonly citations: readonly MockRagCitation[]
  readonly conversationId: string
}

export interface MockRagQueryRequest {
  readonly query: string
  readonly conversationId?: string
  readonly topK?: number
}

const NOW = new Date().toISOString()

// ---------------------------------------------------------------------------
// Citations reused across scenarios
// ---------------------------------------------------------------------------

export const MOCK_CITATION_UO2_MECHANICAL: MockRagCitation = {
  id: "cit-uo2-mech-001",
  source: "Smirnov 2014 — UO2 力学性能综述",
  excerpt:
    "UO2 在室温下的杨氏模量约为 200 GPa，泊松比约 0.31，晶体结构为萤石型面心立方。",
  confidence: 0.92,
  url: "https://example.org/papers/smirnov-2014-uo2-mechanical.pdf",
}

export const MOCK_CITATION_UO2_THERMAL: MockRagCitation = {
  id: "cit-uo2-therm-002",
  source: "NFM-DOC-2023-018 燃料芯块热导率",
  excerpt:
    "UO2 的热导率随温度升高而下降，从 300 K 的 ~9 W/(m·K) 降至 1500 K 的 ~2.5 W/(m·K)。",
  confidence: 0.81,
}

export const MOCK_CITATION_ZR_ALLOY: MockRagCitation = {
  id: "cit-zr-alloy-003",
  source: "Zr-4 合金腐蚀数据库 (NFM-CORR-2024)",
  excerpt:
    "Zr-4 合金在 360 ℃ 高温水中的腐蚀速率约为 35 mg/(dm²·d)，氧化膜以单斜 ZrO₂ 为主。",
  confidence: 0.68,
  url: "https://example.org/datasets/zr4-corrosion-2024.csv",
}

// ---------------------------------------------------------------------------
// Normal scenario responses
// ---------------------------------------------------------------------------

export const MOCK_QUERY_RESPONSE_UO2: MockRagQueryResponse = {
  answer:
    "UO2（二氧化铀）是目前商业压水堆和沸水堆最常用的核燃料形式。" +
    "它具有萤石型面心立方结构，室温下密度约 10.97 g/cm³，熔点约 3120 K。" +
    "在中子辐照下会发生肿胀和裂变气体释放，是限制燃料棒寿命的主要因素。",
  citations: [MOCK_CITATION_UO2_MECHANICAL, MOCK_CITATION_UO2_THERMAL],
  conversationId: "conv-mock-001",
}

export const MOCK_QUERY_RESPONSE_ZR: MockRagQueryResponse = {
  answer:
    "Zr-4 合金是压水堆燃料棒的包壳材料，主要成分为 Zr + 1.5% Sn + 0.2% Fe + 0.1% Cr。" +
    "它具有低的热中子吸收截面（~0.2 b）和良好的耐高温水腐蚀性能。",
  citations: [MOCK_CITATION_ZR_ALLOY],
  conversationId: "conv-mock-002",
}

/**
 * Empty-answer response — common when the query doesn't match any
 * indexed source; the UI must still render gracefully with no citations.
 */
export const MOCK_QUERY_RESPONSE_EMPTY: MockRagQueryResponse = {
  answer: "未在知识图谱中找到与该查询匹配的答案。",
  citations: [],
  conversationId: "conv-mock-empty-003",
}

// ---------------------------------------------------------------------------
// Error scenario response
// ---------------------------------------------------------------------------

export const MOCK_RAG_SERVER_ERROR: {
  readonly detail: string
} = {
  detail: "LightRAG 服务暂时不可用，请稍后重试。",
}

// ---------------------------------------------------------------------------
// Request capture helpers
// ---------------------------------------------------------------------------

/** Stable 8-char conversation id suffix to mirror backend uuid output. */
export function buildConversationId(prefix = "conv-mock"): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
}

/** Echo timestamps into a citation for tests that need deterministic ordering. */
export function withTimestamp<T extends MockRagCitation>(citation: T): T {
  return { ...citation, id: `${citation.id}-${NOW}` }
}
