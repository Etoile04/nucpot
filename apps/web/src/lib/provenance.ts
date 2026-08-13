/**
 * Provenance badge logic for the literature DetailPanel (NFM-2217).
 *
 * Maps backend `provenance: string[]` tokens to human-readable labels and
 * Ant Design Tag colours, with client-side precedence for multi-token items.
 *
 * Contract mirrors `nfm_db.services.provenance` on the backend:
 *   - Three canonical tokens: "llm", "manual", "mineru"
 *   - Precedence: manual > mineru > llm
 *   - Empty / unrecognised → unknown
 */

// ── Types ────────────────────────────────────────────────────────────────

export interface ProvenanceBadge {
  readonly label: string
  readonly color: string
}

// ── Constants ────────────────────────────────────────────────────────────

/** All known provenance tokens in precedence order (highest first). */
const KNOWN_PROVENANCE: readonly string[] = ["manual", "mineru", "llm"]

const PROVENANCE_BADGE_MAP: Readonly<Record<string, ProvenanceBadge>> = {
  llm: { label: "LLM提取", color: "blue" },
  manual: { label: "手动", color: "orange" },
  mineru: { label: "MinerU图", color: "green" },
} as const

/** Badge for items with no recognised provenance. */
const UNKNOWN_BADGE: ProvenanceBadge = { label: "来源未知", color: "default" }

/** Badge for KG edges (based on source_type, not provenance). */
export const KG_EDGE_BADGE: ProvenanceBadge = { label: "KG关系", color: "purple" }

/** Ordered section keys for provenance-based grouping (display order). */
export const PROVENANCE_SECTION_ORDER: readonly string[] = [
  "llm",
  "mineru",
  "manual",
  "unknown",
]

// ── Public helpers ─────────────────────────────────────────────────────────

/**
 * Resolve a multi-token provenance array to a single display badge using
 * client-side precedence: manual > mineru > llm.
 *
 * @param tokens - Raw provenance array from the API (may be empty).
 * @returns The highest-precedence badge, or UNKNOWN_BADGE if no token matches.
 */
export function resolveProvenanceBadge(tokens: readonly string[]): ProvenanceBadge {
  for (const token of KNOWN_PROVENANCE) {
    if (tokens.includes(token)) {
      const badge = PROVENANCE_BADGE_MAP[token]
      if (badge) return badge
    }
  }
  return UNKNOWN_BADGE
}

/**
 * Resolve a multi-token provenance array to the primary token key for
 * grouping. Returns "unknown" when no token matches.
 *
 * @param tokens - Raw provenance array from the API.
 */
export function resolveProvenanceKey(tokens: readonly string[]): string {
  for (const token of KNOWN_PROVENANCE) {
    if (tokens.includes(token)) {
      return token
    }
  }
  return "unknown"
}

/**
 * Get the section header label for a provenance key (used in Collapse).
 */
export function getProvenanceSectionLabel(key: string): string {
  if (key === "unknown") return UNKNOWN_BADGE.label
  return PROVENANCE_BADGE_MAP[key]?.label ?? UNKNOWN_BADGE.label
}

/**
 * Get the Tag colour for a provenance key.
 */
export function getProvenanceColor(key: string): string {
  if (key === "unknown") return UNKNOWN_BADGE.color
  return PROVENANCE_BADGE_MAP[key]?.color ?? UNKNOWN_BADGE.color
}
