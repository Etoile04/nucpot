'use client'

interface PaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}

export default function Pagination({ currentPage, totalPages, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null

  const getVisiblePages = (): (number | 'ellipsis')[] => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)

    const pages: (number | 'ellipsis')[] = [1]
    if (currentPage > 3) pages.push('ellipsis')

    const start = Math.max(2, currentPage - 1)
    const end = Math.min(totalPages - 1, currentPage + 1)
    for (let i = start; i <= end; i++) pages.push(i)

    if (currentPage < totalPages - 2) pages.push('ellipsis')
    pages.push(totalPages)

    return pages
  }

  const btnBase = 'px-3 py-1.5 rounded-lg text-sm transition'
  const btnActive = `${btnBase} bg-blue-600 text-white`
  const btnInactive = `${btnBase} bg-gray-800 text-gray-300 hover:bg-gray-700`
  const btnDisabled = `${btnBase} bg-gray-800 text-gray-600 cursor-not-allowed`

  return (
    <nav aria-label="分页导航" className="flex items-center justify-center gap-1.5 mt-6">
      <button
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 1}
        className={currentPage === 1 ? btnDisabled : btnInactive}
        aria-label="上一页"
      >
        ← 上一页
      </button>

      {getVisiblePages().map((p, i) =>
        p === 'ellipsis' ? (
          <span key={`e${i}`} className="px-2 text-gray-500">...</span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            className={p === currentPage ? btnActive : btnInactive}
            aria-label={`第 ${p} 页`}
            aria-current={p === currentPage ? 'page' : undefined}
          >
            {p}
          </button>
        )
      )}

      <button
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage === totalPages}
        className={currentPage === totalPages ? btnDisabled : btnInactive}
        aria-label="下一页"
      >
        下一页 →
      </button>
    </nav>
  )
}
