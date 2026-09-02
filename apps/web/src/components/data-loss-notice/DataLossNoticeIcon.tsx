"use client"

/**
 * Inline disclosure icon — 18px information-with-vertical-bar glyph.
 *
 * Spec §3.1 / §2.5 — uses `--warning-border` token (already on the
 * global stylesheet). The component is presentation-only and ships
 * zero JS to the consumer.
 *
 * Rendered as an inline `<svg>` (rather than a CSS background image)
 * so screen readers can label it via `<title>` and so the inline
 * variant respects the surrounding font-size for the surrounding label.
 */

export interface DataLossNoticeIconProps {
  /** Pixel size of the icon. Defaults to 18 per spec §2.5. */
  readonly size?: number
  /** Optional title override; falls back to a generic aria-label. */
  readonly title?: string
}

export function DataLossNoticeIcon({
  size = 18,
  title = "Source attribution disclosure",
}: DataLossNoticeIconProps): JSX.Element {
  const stroke = "var(--warning-border, #b45309)"
  return (
    <svg
      role="img"
      aria-label={title}
      width={size}
      height={size}
      viewBox="0 0 18 18"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      data-testid="data-loss-notice-icon"
    >
      <title>{title}</title>
      <circle cx="9" cy="9" r="7.5" stroke={stroke} strokeWidth="1.25" />
      <line
        x1="9"
        y1="7"
        x2="9"
        y2="11"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <circle cx="9" cy="5" r="0.85" fill={stroke} />
    </svg>
  )
}