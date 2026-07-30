/**
 * ProvenanceBadge — display-only data-provenance indicator.
 *
 * Labels each extraction result with where its value came from, so a reader
 * can tell machine-inferred values (which need verification) apart from
 * human-entered ones (which are authoritative).
 *
 * Provenance is always read from a server-provided field. This component
 * never infers provenance from confidence, review status, or any other
 * client-side heuristic — an absent field renders as an explicit
 * "unknown" state rather than a guess.
 *
 * Spec: NFM-2237 (epic item #8 of NFM-2209)
 */

/** Resolved provenance of a single extraction item. */
export type ProvenanceKind = 'llm' | 'manual' | 'mineru' | 'unknown'

/** User-facing label per provenance kind. */
export const PROVENANCE_LABELS: Readonly<Record<ProvenanceKind, string>> = {
  llm: 'LLM提取',
  manual: '手动',
  mineru: 'MinerU图',
  unknown: '来源未知',
}

/**
 * Precedence applied when the server reports more than one source.
 *
 * Manual wins: a value that was LLM- or MinerU-extracted and then corrected
 * by a human is a human-authored value, so it must not display stale
 * machine provenance (NFM-2237 requirement 4).
 */
const PRECEDENCE: ReadonlyArray<Exclude<ProvenanceKind, 'unknown'>> = [
  'manual',
  'mineru',
  'llm',
]

function normalizeToken(token: unknown): ProvenanceKind {
  if (typeof token !== 'string') return 'unknown'
  const value = token.trim().toLowerCase()
  if (value === 'manual') return 'manual'
  if (value === 'mineru') return 'mineru'
  if (value === 'llm') return 'llm'
  return 'unknown'
}

/**
 * Normalize a raw server provenance value into exactly one ProvenanceKind.
 *
 * Accepts a single value ("manual"), an array (["llm", "manual"]), or a
 * comma-joined string ("llm,manual"). Anything unrecognized, empty, or
 * absent resolves to 'unknown' — never to a guessed source.
 */
export function resolveProvenance(raw: unknown): ProvenanceKind {
  const tokens: readonly unknown[] = Array.isArray(raw)
    ? raw
    : typeof raw === 'string'
      ? raw.split(',')
      : []

  const present = new Set(tokens.map(normalizeToken))
  return PRECEDENCE.find((kind) => present.has(kind)) ?? 'unknown'
}

interface ProvenanceStyle {
  /** Decorative glyph — a non-color cue, so provenance stays
   *  distinguishable without relying on hue. */
  readonly glyph: string
  readonly textColor: string
  readonly bgColor: string
  readonly borderColor: string
  /** Solid border reads as authoritative; dashed reads as provisional. */
  readonly borderStyle: 'border-solid' | 'border-dashed'
  readonly fontWeight: string
  readonly title: string
}

const STYLES: Readonly<Record<ProvenanceKind, ProvenanceStyle>> = {
  // Human-authored — most visually authoritative.
  manual: {
    glyph: '◆',
    textColor: 'text-emerald-300',
    bgColor: 'bg-emerald-900/50',
    borderColor: 'border-emerald-500',
    borderStyle: 'border-solid',
    fontWeight: 'font-semibold',
    title: '人工录入或人工校正 — 权威值',
  },
  // Figure/image derived — distinct from both text-LLM and human entry.
  mineru: {
    glyph: '▣',
    textColor: 'text-sky-300',
    bgColor: 'bg-sky-900/50',
    borderColor: 'border-sky-600',
    borderStyle: 'border-solid',
    fontWeight: 'font-normal',
    title: 'MinerU 图像/图表提取 — 建议人工核验',
  },
  // Machine-inferred — reads as provisional.
  llm: {
    glyph: '◇',
    textColor: 'text-amber-300',
    bgColor: 'bg-amber-900/50',
    borderColor: 'border-amber-600',
    borderStyle: 'border-dashed',
    fontWeight: 'font-normal',
    title: 'LLM 自动提取 — 建议人工核验',
  },
  // Server did not report provenance. Surfaced explicitly rather than
  // hidden, so a missing field stays visible instead of silently reading
  // as machine-extracted.
  unknown: {
    glyph: '?',
    textColor: 'text-gray-400',
    bgColor: 'bg-gray-700/60',
    borderColor: 'border-gray-600',
    borderStyle: 'border-dashed',
    fontWeight: 'font-normal',
    title: '数据来源未提供（服务端未返回 provenance 字段）',
  },
}

const SIZE_CLASSES: Readonly<Record<'sm' | 'md', string>> = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-2.5 py-1 text-sm',
}

export interface ProvenanceBadgeProps {
  /** Raw provenance value from the server. Unknown or absent renders the
   *  explicit "来源未知" state. */
  readonly provenance: unknown
  /** Badge size variant. Default: 'sm' */
  readonly size?: 'sm' | 'md'
  /** Additional CSS classes */
  readonly className?: string
}

export function ProvenanceBadge({
  provenance,
  size = 'sm',
  className = '',
}: ProvenanceBadgeProps) {
  const kind = resolveProvenance(provenance)
  const style = STYLES[kind]
  const label = PROVENANCE_LABELS[kind]

  return (
    <span
      role="status"
      aria-label={`数据来源: ${label}`}
      title={style.title}
      className={[
        'inline-flex items-center gap-1 rounded-full border',
        style.bgColor,
        style.textColor,
        style.borderColor,
        style.borderStyle,
        style.fontWeight,
        SIZE_CLASSES[size],
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <span aria-hidden="true">{style.glyph}</span>
      <span>{label}</span>
    </span>
  )
}
