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
  /**
   * BUG-02 three-state (NFM-4079): when a verification job completed but a
   * property had no reference value, NFM-3873 returns
   * `reference_flag: "reference_missing"` and overall_grade stays null.
   * The badge must distinguish "computed but no reference" from
   * "never verified" — pass the flag here so operators see the data gap
   * instead of a misleading "未验证" on a measured potential.
   */
  referenceFlag?: string | null | undefined
  /** Optional size variant: 'sm' for inline, 'lg' for detail page hero */
  size?: 'sm' | 'lg'
}

export default function VerificationBadge({
  grade,
  referenceFlag,
  size = 'sm',
}: VerificationBadgeProps) {
  const smBase = 'px-2 py-0.5 rounded text-xs font-medium'
  const lgBase = 'px-4 py-1.5 rounded-lg text-sm font-medium'

  // Three-state: grade → graded badge; reference_missing → amber
  // "computed, reference missing"; otherwise gray "未验证".
  if (!grade) {
    if (referenceFlag === 'reference_missing') {
      return (
        <span
          className={`inline-flex items-center gap-1 ${
            size === 'lg' ? lgBase : smBase
          } bg-amber-900/40 text-amber-300 border border-amber-700`}
          title="已计算，但缺少参考值（reference_missing）——无法评级。请补充参考数据后重新验证。"
        >
          ◐ 缺参考值
        </span>
      )
    }
    return (
      <span
        className={`inline-flex items-center ${
          size === 'lg' ? lgBase : smBase
        } bg-gray-700 text-gray-400`}
      >
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
    <span
      className={`inline-flex items-center gap-1 ${
        size === 'lg' ? lgClasses : smClasses
      } ${colorClass}`}
    >
      {upper} {label}
    </span>
  )
}
