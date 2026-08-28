/**
 * SkeletonTable: painted skeleton for the ontology list page.
 * Uses Tailwind classes — no inline styles.
 */

interface SkeletonTableProps {
  readonly rows?: number
}

const COLUMNS = ['50%', '18%', '15%', '12%', '10%']

export function SkeletonTable({ rows = 8 }: SkeletonTableProps) {
  return (
    <div
      className="onto-skeleton-table w-full"
      role="status"
      aria-label="Loading ontology list"
    >
      <div className="flex gap-3 px-4 py-2 border-b border-gray-700">
        {COLUMNS.map((w, i) => (
          <div key={i} className="h-3.5 rounded-sm bg-gray-700" style={{ width: w }} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, rowIdx) => (
        <div key={rowIdx} className="flex gap-3 px-4 py-3 border-b border-gray-700 min-h-9">
          {COLUMNS.map((w, colIdx) => (
            <div
              key={colIdx}
              className="h-3.5 rounded-sm bg-gray-800"
              style={{ width: `${parseFloat(w) * (60 + Math.random() * 30)}%`, maxWidth: w }}
            />
          ))}
        </div>
      ))}
      <span className="sr-only">Loading...</span>
    </div>
  )
}
