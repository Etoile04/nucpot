/**
 * ConfidenceMeter -- horizontal bar indicating confidence 0-1.
 *
 * Color thresholds (WCAG AA compliant contrast):
 *   < 0.4  -> red-500   (danger)
 *   0.4-0.7 -> amber-500 (warning)
 *   > 0.7  -> emerald-500 (success)
 *
 * Spec: NFM-3704
 */

"use client"

interface ConfidenceMeterProps {
  /** Confidence value between 0 and 1. Clamped. */
  readonly value: number
  /** Additional CSS class on the outer wrapper. */
  readonly className?: string
}

function getColorClass(value: number): string {
  if (value > 0.7) return 'bg-emerald-500'
  if (value >= 0.4) return 'bg-amber-500'
  return 'bg-red-500'
}

function getLabel(value: number): string {
  if (value > 0.7) return 'high confidence'
  if (value >= 0.4) return 'medium confidence'
  return 'low confidence'
}

export function ConfidenceMeter({ value, className = '' }: ConfidenceMeterProps) {
  const clamped = Math.max(0, Math.min(1, value))
  const percent = Math.round(clamped * 100)
  const colorClass = getColorClass(clamped)
  const label = getLabel(clamped)

  return (
    <div className={['inline-flex items-center gap-2', className].filter(Boolean).join(' ')}>
      <div
        role="meter"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-label={`Confidence: ${label}`}
        className={['relative h-2 w-24 rounded-full bg-gray-700 overflow-hidden'].join(' ')}
      >
        <div
          className={['absolute inset-y-0 left-0 rounded-full transition-all duration-300', colorClass].join(' ')}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-mono text-gray-300 tabular-nums" aria-hidden="true">
        {percent}%
      </span>
    </div>
  )
}
