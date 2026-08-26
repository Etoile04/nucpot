/**
 * Gap decision types for bulk action toolbar.
 *
 * These types describe the contract between the BulkActionToolbar,
 * the keyboard shortcut hook, and the bulk decisions API.
 */

// ─── Decision ─────────────────────────────────────────────────────────

export type GapDecision = 'accepted' | 'rejected' | 'deferred'

export interface GapCandidate {
  readonly candidate_id: string
  readonly confidence: number
  readonly label?: string
}

// ─── Bulk API request/response ─────────────────────────────────────────

export interface BulkDecisionRequest {
  readonly decisions: ReadonlyArray<{
    readonly candidate_id: string
    readonly decision: GapDecision
  }>
}

export interface BulkDecisionResultItem {
  readonly candidate_id: string
  readonly decision: GapDecision
  readonly decided_at: string
  readonly reviewer_id: string
  readonly error?: string
}

export interface BulkDecisionResponse {
  readonly results: ReadonlyArray<BulkDecisionResultItem>
}

// ─── Toolbar props ─────────────────────────────────────────────────────

export interface BulkActionToolbarProps {
  /** Currently selected candidate rows */
  readonly selectedItems: ReadonlyArray<GapCandidate>
  /** Minimum confidence for "Accept >= threshold" filter (default 0.7) */
  readonly confidenceThreshold: number
  /** Callback after a successful bulk operation */
  readonly onSuccess: () => void
  /** Callback on failure (error message) */
  readonly onError: (message: string) => void
  /** Whether a bulk operation is in-flight */
  readonly loading?: boolean
}

// ─── Keyboard shortcut config ──────────────────────────────────────────

export interface KeyboardShortcutDef {
  readonly key: string
  readonly label: string
  readonly description: string
  readonly requiresShift?: boolean
}

export const GAP_REVIEW_SHORTCUTS: ReadonlyArray<KeyboardShortcutDef> = [
  { key: 'a', label: 'A', description: '接受 / Accept' },
  { key: 'r', label: 'R', description: '拒绝 / Reject' },
  { key: 'd', label: 'D', description: '推迟 / Defer' },
  { key: 'Escape', label: 'Esc', description: '关闭抽屉 / Close drawer' },
  { key: 'A', label: 'Shift+A', description: '全部接受 / Accept all visible', requiresShift: true },
  { key: '?', label: '?', description: '快捷键帮助 / Shortcuts help' },
] as const
