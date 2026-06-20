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

interface VerificationBadgeProps {
  grade: string | null | undefined
  /** Optional size variant: 'sm' for inline, 'lg' for detail page hero */
  size?: 'sm' | 'lg'
}

export default function VerificationBadge({ grade, size = 'sm' }: VerificationBadgeProps) {
  if (!grade) {
    const smClasses = 'px-2 py-0.5 rounded text-xs font-medium bg-gray-700 text-gray-400'
    const lgClasses = 'px-4 py-1.5 rounded-lg text-sm font-medium bg-gray-700 text-gray-400'
    return (
      <span className={`inline-flex items-center ${size === 'lg' ? lgClasses : smClasses}`}>
        未验证
      </span>
    )
  }

  const upper = grade.toUpperCase()
  const colorClass = GRADE_COLORS[upper] || 'bg-gray-600 text-gray-300'
  const label = GRADE_LABELS[upper] || upper

  const smClasses = 'px-2 py-0.5 rounded text-xs font-bold'
  const lgClasses = 'px-4 py-1.5 rounded-lg text-base font-bold'

  return (
    <span className={`inline-flex items-center gap-1 ${size === 'lg' ? lgClasses : smClasses} ${colorClass}`}>
      {upper} {label}
    </span>
  )
}
