/**
 * ErrorPanel: surface panel with retry and optional request-id copy.
 * Uses Tailwind classes — no inline styles.
 */
import { useCallback, useState } from 'react'

interface ErrorPanelProps {
  readonly httpStatus?: number
  readonly requestId?: string
  readonly message?: string
  readonly onRetry?: () => void
  readonly variant?: 'list' | 'detail' | 'edit'
}

const COPY: Record<string, { zh: string; en: string }> = {
  list: { zh: '无法加载本体列表', en: "Couldn't load the ontology list" },
  detail: { zh: '加载失败', en: 'Failed to load' },
  edit: { zh: '保存失败', en: 'Failed to save' },
}

export function ErrorPanel({
  httpStatus,
  requestId,
  message,
  onRetry,
  variant = 'list',
}: ErrorPanelProps) {
  const [copied, setCopied] = useState(false)
  const labels = (COPY[variant] ?? COPY['list'])!
  const statusSuffix = httpStatus ? ` (${httpStatus})` : ''
  const detail = message ?? `${labels.en}${statusSuffix}.`

  const copyId = useCallback(() => {
    if (!requestId) return
    void navigator.clipboard.writeText(requestId).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [requestId])

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="p-10 bg-gray-800 rounded-lg border border-red-500/60 text-center max-w-lg mx-auto"
    >
      <svg
        width={48}
        height={48}
        viewBox="0 0 48 48"
        fill="none"
        aria-hidden="true"
        className="mb-3 text-red-400 mx-auto block"
      >
        <rect x="4" y="4" width="40" height="40" rx="4" stroke="currentColor" strokeWidth={2} />
        <path d="M16 20h16M16 28h10" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
        <path d="M24 4v8" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
      </svg>
      <p className="text-gray-200 text-base m-0">{detail}</p>
      <div className="flex justify-center gap-3 mt-4">
        {onRetry && (
          <button
            onClick={onRetry}
            className="px-4 py-2 rounded border border-gray-500 bg-gray-700 text-gray-200 cursor-pointer text-sm hover:bg-gray-600 transition-colors"
          >
            Retry
          </button>
        )}
        {requestId && (
          <button
            onClick={copyId}
            className="px-4 py-2 rounded border border-gray-600 bg-transparent text-gray-400 cursor-pointer text-sm hover:text-gray-200 transition-colors"
          >
            {copied ? 'Copied' : 'Copy request ID'}
          </button>
        )}
      </div>
    </div>
  )
}
