/**
 * StatusChip: pill-shaped status badge bound to ontology status tokens.
 * Per NFM-3550 §2 — never hand-pick colors, always use --onto-status-*.
 */
'use client'

import type { OntologyVersionStatus } from '../types'

interface StatusChipProps {
  readonly status: OntologyVersionStatus
}

const STATUS_TOKENS: Record<OntologyVersionStatus, string> = {
  draft: 'var(--onto-status-draft)',
  published: 'var(--onto-status-published)',
  deprecated: 'var(--onto-status-deprecated)',
}

const LABELS: Record<OntologyVersionStatus, string> = {
  draft: 'Draft',
  published: 'Published',
  deprecated: 'Deprecated',
}

export function StatusChip({ status }: StatusChipProps) {
  return (
    <span
      role="status"
      aria-label={`Status: ${LABELS[status]}`}
      style={{
        display: 'inline-block',
        padding: '2px var(--onto-space-2)',
        borderRadius: 'var(--onto-radius-pill)',
        backgroundColor: STATUS_TOKENS[status],
        color: 'var(--onto-ink-strong)',
        fontSize: 'var(--onto-fs-xs)',
        fontWeight: 500,
        lineHeight: '1.4',
        whiteSpace: 'nowrap',
        letterSpacing: '0.01em',
      }}
    >
      {LABELS[status]}
    </span>
  )
}
