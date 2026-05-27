'use client'

const GRADE_COLORS: Record<string, string> = {
  A: 'bg-green-600 text-white',
  B: 'bg-blue-600 text-white',
  C: 'bg-yellow-600 text-black',
  D: 'bg-orange-600 text-white',
  F: 'bg-red-600 text-white',
}

const GRADE_LABELS: Record<string, string> = {
  A: '优秀',
  B: '良好',
  C: '一般',
  D: '较差',
  F: '不合格',
}

export default function VerificationBadge({ grade }: { grade: string | null | undefined }) {
  if (!grade) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-700 text-gray-400">
        未验证
      </span>
    )
  }

  const upper = grade.toUpperCase()
  const colorClass = GRADE_COLORS[upper] || 'bg-gray-600 text-gray-300'
  const label = GRADE_LABELS[upper] || upper

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ${colorClass}`}>
      {upper} {label}
    </span>
  )
}
