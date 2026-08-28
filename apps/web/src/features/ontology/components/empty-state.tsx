/**
 * EmptyState: domain-aware placeholder with optional action.
 * Uses Tailwind classes — no inline styles.
 */

interface EmptyStateProps {
  readonly title: string
  readonly description?: string
  readonly action?: React.ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center justify-center py-16 px-6 text-center"
    >
      <svg
        width={64}
        height={64}
        viewBox="0 0 64 64"
        fill="none"
        aria-hidden="true"
        className="mb-4 text-gray-600"
      >
        <rect x="8" y="12" width="48" height="40" rx="4" stroke="currentColor" strokeWidth={2} />
        <line x1="16" y1="24" x2="48" y2="24" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
        <line x1="16" y1="32" x2="48" y2="32" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
        <line x1="16" y1="40" x2="48" y2="40" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
      </svg>
      <p className="text-lg font-semibold text-gray-200 m-0">
        {title}
      </p>
      {description && (
        <p className="text-sm text-gray-400 mt-1 mb-0 max-w-[400px]">
          {description}
        </p>
      )}
      {action && <span className="mt-3 inline-block">{action}</span>}
    </div>
  )
}
