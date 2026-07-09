/**
 * ConfidenceBadge — display-only confidence indicator.
 *
 * Maps a numeric score (0.0–1.0) to a color-coded pill with optional label.
 * Spec: NFM-848 §2.4
 */

interface ConfidenceTier {
  readonly label: string
  readonly textColor: string
  readonly bgColor: string
  readonly dotColor: string
}

interface ConfidenceBadgeProps {
  /** Confidence value between 0.0 and 1.0 */
  readonly value: number
  /** Badge size variant. Default: 'sm' */
  readonly size?: 'sm' | 'md'
  /** Show the text label (高/中/低). Default: false */
  readonly showLabel?: boolean
  /** Additional CSS classes */
  readonly className?: string
}

const TIERS: ReadonlyArray<ConfidenceTier> = [
  {
    label: '高',
    textColor: 'text-emerald-400',
    bgColor: 'bg-emerald-900/50',
    dotColor: 'bg-emerald-400',
  },
  {
    label: '中',
    textColor: 'text-amber-400',
    bgColor: 'bg-amber-900/50',
    dotColor: 'bg-amber-400',
  },
  {
    label: '低',
    textColor: 'text-red-400',
    bgColor: 'bg-red-900/50',
    dotColor: 'bg-red-400',
  },
]

function getTier(value: number): ConfidenceTier {
  if (value > 0.8) return TIERS[0]
  if (value >= 0.6) return TIERS[1]
  return TIERS[2]
}

const SIZE_CLASSES: Record<string, string> = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-2.5 py-1 text-sm',
}

export function getConfidenceLabel(value: number): string {
  return getTier(value).label
}

export { type ConfidenceBadgeProps, type ConfidenceTier }

export function ConfidenceBadge({
  value,
  size = 'sm',
  showLabel = false,
  className = '',
}: ConfidenceBadgeProps) {
  const tier = getTier(value)
  const sizeClass = SIZE_CLASSES[size]
  const tooltipText = `${value.toFixed(2)} — ${tier.label}置信度`

  return (
    <span
      role="status"
      aria-label={`置信度: ${value.toFixed(2)}, ${tier.label}`}
      title={tooltipText}
      className={[
        'inline-flex items-center gap-1 rounded-full font-mono font-medium',
        tier.bgColor,
        tier.textColor,
        sizeClass,
        className,
      ].filter(Boolean).join(' ')}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${tier.dotColor}`}
        aria-hidden="true"
      />
      {showLabel && <span>{tier.label}</span>}
    </span>
  )
}
