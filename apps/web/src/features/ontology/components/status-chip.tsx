/**
 * StatusChip: pill-shaped status badge for ontology version statuses.
 * Uses Tailwind classes — no inline styles.
 */
import type { OntologyVersionStatus } from '../types'

const STATUS_CLASSES: Record<OntologyVersionStatus, string> = {
  draft: 'bg-amber-900/40 text-amber-300',
  published: 'bg-emerald-900/40 text-emerald-300',
  deprecated: 'bg-gray-700/40 text-gray-400',
}

const LABELS: Record<OntologyVersionStatus, string> = {
  draft: 'Draft',
  published: 'Published',
  deprecated: 'Deprecated',
}

interface StatusChipProps {
  readonly status: OntologyVersionStatus
}

export function StatusChip({ status }: StatusChipProps) {
  return (
    <span
      role="status"
      aria-label={`Status: ${LABELS[status]}`}
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium leading-tight whitespace-nowrap tracking-wide ${STATUS_CLASSES[status]}`}
    >
      {LABELS[status]}
    </span>
  )
}
