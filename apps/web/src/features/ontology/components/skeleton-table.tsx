/**
 * SkeletonTable: painted skeleton for the ontology list page.
 *
 * Per NFM-3550 §2 and §4.1 — uses surface-inset rectangles
 * sized to actual cell widths; no shimmer animation.
 */
'use client'

interface SkeletonTableProps {
  readonly rows?: number
}

const COLUMNS = ['50%', '18%', '15%', '12%', '10%']

export function SkeletonTable({ rows = 8 }: SkeletonTableProps) {
  return (
    <div
      className="onto-skeleton-table"
      role="status"
      aria-label="Loading ontology list"
      style={{ width: '100%' }}
    >
      {/* Header row */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--onto-space-3)',
          padding: 'var(--onto-space-2) var(--onto-space-4)',
          borderBottom: '1px solid var(--onto-border-soft)',
        }}
      >
        {COLUMNS.map((w, i) => (
          <div
            key={i}
            style={{
              width: w,
              height: 14,
              backgroundColor: 'var(--onto-surface-3)',
              borderRadius: 'var(--onto-radius-xs)',
            }}
          />
        ))}
      </div>
      {/* Body rows */}
      {Array.from({ length: rows }, (_, rowIdx) => (
        <div
          key={rowIdx}
          style={{
            display: 'flex',
            gap: 'var(--onto-space-3)',
            padding: 'var(--onto-space-3) var(--onto-space-4)',
            borderBottom: '1px solid var(--onto-border-soft)',
            minHeight: 36,
          }}
        >
          {COLUMNS.map((w, colIdx) => (
            <div
              key={colIdx}
              style={{
                width: `${parseFloat(w) * (60 + Math.random() * 30)}%`,
                maxWidth: w,
                height: 13,
                backgroundColor: 'var(--onto-surface-inset)',
                borderRadius: 'var(--onto-radius-xs)',
              }}
            />
          ))}
        </div>
      ))}
      <span className="sr-only">Loading...</span>
    </div>
  )
}
