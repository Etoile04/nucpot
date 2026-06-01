// NFMD Review Module - Types & Constants

export interface ReviewParam {
  id: string
  name: string
  name_zh: string | null
  category: string
  subcategory: string | null
  value_type: string
  value_scalar: number | null
  value_min: number | null
  value_max: number | null
  value_expr: string | null
  value_list: unknown | null
  value_text: string | null
  unit: string | null
  material_raw: string | null
  temperature_k: number | null
  confidence: string | null
  source_file: string | null
  notes: string | null
  review_status: string | null
  review_reason: string | null
  reviewed_at: string | null
}

export type ReviewStatus =
  | 'needs_data'    // 空壳：缺数据
  | 'needs_review'  // 低置信度
  | 'pending'       // 待处理
  | 'approved'      // 人工通过
  | 'auto_approved' // 自动通过
  | 'rejected'      // 拒绝/待删
  | 'duplicate'     // 重复

export interface ReviewStats {
  by_status: Record<string, number>
  by_value_type: Record<string, number>
  top_materials: Record<string, number>
  total_in_review: number
  total_params: number
}

export const STATUS_CONFIG: Record<ReviewStatus | string, {
  label: string
  color: string
  bgColor: string
  priority: number
}> = {
  needs_data:    { label: '缺数据',   color: 'text-orange-400',   bgColor: 'bg-orange-900/50',   priority: 1 },
  pending:       { label: '待处理',   color: 'text-yellow-400',  bgColor: 'bg-yellow-900/50',  priority: 2 },
  needs_review:  { label: '需人工审', color: 'text-purple-400',  bgColor: 'bg-purple-900/50',  priority: 3 },
  duplicate:     { label: '重复',     color: 'text-blue-400',    bgColor: 'bg-blue-900/50',     priority: 4 },
  rejected:      { label: '已拒绝',   color: 'text-red-400',     bgColor: 'bg-red-900/50',      priority: 5 },
  approved:      { label: '已通过',   color: 'text-green-400',   bgColor: 'bg-green-900/50',    priority: 6 },
  auto_approved: { label: '自动通过', color: 'text-gray-400',    bgColor: 'bg-gray-800/50',     priority: 7 },
}

export const VALUE_TYPE_LABELS: Record<string, string> = {
  scalar: '标量',
  range: '范围',
  expression: '表达式',
  list: '列表',
  text: '文本',
}

export const CONFIDENCE_LABELS: Record<string, { label: string; color: string }> = {
  high:   { label: '高', color: 'text-green-400' },
  medium: { label: '中', color: 'text-yellow-400' },
  low:    { label: '低', color: 'text-red-400' },
}

export interface LiteratureRef {
  id: string
  title: string
  authors: string | null
  journal: string | null
  year: number | null
  doi: string | null
  match_method: string
}

export interface ParamWithContext extends ReviewParam {
  literature: LiteratureRef[]
}

export function formatLiterature(lit: LiteratureRef): string {
  const parts: string[] = []
  if (lit.authors) parts.push(lit.authors)
  if (lit.year) parts.push(`(${lit.year})`)
  if (lit.title) parts.push(lit.title.replace(/_\S+$/, ''))
  if (lit.journal) parts.push(lit.journal)
  return parts.join('. ')
}

export function statusBadge(status: string | null) {
  if (!status) return null
  const cfg = STATUS_CONFIG[status] || { label: status, color: 'text-gray-400', bgColor: 'bg-gray-700' }
  return <span className={`text-xs px-2 py-0.5 rounded ${cfg.bgColor} ${cfg.color}`}>{cfg.label}</span>
}

export function valueDisplay(p: ReviewParam): string {
  switch (p.value_type) {
    case 'scalar': return p.value_scalar != null ? String(p.value_scalar) : '—'
    case 'range': {
      const parts = []
      if (p.value_min != null) parts.push(String(p.value_min))
      if (p.value_max != null) parts.push(String(p.value_max))
      return parts.length ? parts.join(' ~ ') : '—'
    }
    case 'expression': return p.value_expr || '—'
    case 'list': return Array.isArray(p.value_list) ? `[${p.value_list.length} items]` : '—'
    case 'text': return p.value_text || '—'
    default: return '—'
  }
}
