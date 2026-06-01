'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import { supabaseAdmin } from '@/lib/supabase'
import type { ReviewParam, ReviewStatus, LiteratureRef, ParamWithContext } from '@/lib/nfmd-review'
import { STATUS_CONFIG, VALUE_TYPE_LABELS, CONFIDENCE_LABELS, statusBadge, valueDisplay, formatLiterature } from '@/lib/nfmd-review'

type TabId = 'needs_data' | 'needs_review' | 'all'

const PAGE_SIZE = 30

export default function ReviewParamsPage() {
  const router = useRouter()
  const { profile, loading: authLoading } = useAuth()

  const [activeTab, setActiveTab] = useState<TabId>('needs_data')
  const [params, setParams] = useState<ReviewParam[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterMaterial, setFilterMaterial] = useState('')

  // Selection
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkAction, setBulkAction] = useState<ReviewStatus | null>(null)

  // Edit modal
  const [editModal, setEditModal] = useState<ReviewParam | null>(null)
  const [editForm, setEditForm] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  // Text context panel (T4a)
  const [contextPanel, setContextPanel] = useState<{ param: ReviewParam; text: string; loading: boolean } | null>(null)

  // PDF viewer panel (T4b)
  const [pdfPanel, setPdfPanel] = useState<{ param: ReviewParam; url: string } | null>(null)

  useEffect(() => {
    if (!isLoading && (!profile || profile.role !== 'admin')) router.push('/')
  }, [authLoading, profile, router])

  const fetchParams = useCallback(async () => {
    setIsLoading(true)
    try {
      const status = activeTab === 'all' ? undefined : activeTab
      const { data, error } = await supabaseAdmin!.rpc('review_queue_params', {
        p_status: status || null,
        p_value_type: filterType || null,
        p_material: filterMaterial || null,
        p_source_file: search || null,
        p_limit: PAGE_SIZE,
        p_offset: (page - 1) * PAGE_SIZE,
      })
      if (error) throw error

      const result = typeof data === 'object' && 'data' in (data as any) ? (data as any) : { data: [], total: 0 }
      const paramsList: ReviewParam[] = result.data || []
      setTotalCount(result.total || 0)
      setSelected(new Set())

      // Fetch literature context for each param
      const enriched = await Promise.all(
        paramsList.map(async (p) => {
          if (!p.source_file) return { ...p, literature: [] }
          try {
            const { data: litData } = await supabaseAdmin!.rpc('review_literature_for_source', {
              p_source_file: p.source_file,
            })
            return { ...p, literature: Array.isArray(litData) ? litData : [] }
          } catch {
            return { ...p, literature: [] }
          }
        })
      )
      setParams(enriched)
    } catch (e: any) {
      console.error('Failed:', e)
    } finally {
      setIsLoading(false)
    }
  }, [activeTab, page, filterType, filterMaterial, search])

  useEffect(() => { if (profile?.role === 'admin') fetchParams() }, [profile, fetchParams])

  // Tab counts
  const [tabCounts, setTabCounts] = useState<Record<string, number>>({})
  useEffect(() => {
    if (profile?.role === 'admin') {
      supabaseAdmin!.rpc('review_stats').then(({ data }) => {
        if (data?.by_status) setTabCounts(data.by_status)
      })
    }
  }, [profile])

  // Bulk actions
  const handleBulkAction = async () => {
    if (!bulkAction || selected.size === 0) return
    setSubmitting(true)
    try {
      await supabaseAdmin!.rpc('review_batch_update', {
        p_ids: Array.from(selected),
        p_status: bulkAction,
        p_reviewer: profile?.username || 'admin',
      })
      fetchParams()
    } catch (e) {
      console.error(e)
    } finally {
      setSubmitting(false)
      setBulkAction(null)
    }
  }

  // Single update
  const fetchTextContext = async (p: ReviewParam) => {
    if (!p.source_file) return
    setContextPanel({ param: p, text: '', loading: true })
    try {
      const res = await fetch(`/api/admin/review/pdf?source_file=${encodeURIComponent(p.source_file)}&keyword=${encodeURIComponent(p.name)}`)
      const data = await res.json()
      setContextPanel({ param: p, text: data.paragraphs?.map((pg: any) => `[p.${pg.page}] ${pg.text}`).join('\n\n') || '未找到相关文本段落', loading: false })
    } catch {
      setContextPanel({ param: p, text: '获取失败', loading: false })
    }
  }

  const openPdf = (p: ReviewParam) => {
    if (!p.source_file) return
    const url = `/api/admin/review/pdf?source_file=${encodeURIComponent(p.source_file)}`
    setPdfPanel({ param: p, url })
  }

  const openEdit = (p: ReviewParam) => {
    setEditForm({
      name: p.name || '',
      value_scalar: p.value_scalar != null ? String(p.value_scalar) : '',
      value_min: p.value_min != null ? String(p.value_min) : '',
      value_max: p.value_max != null ? String(p.value_max) : '',
      value_expr: p.value_expr || '',
      value_text: p.value_text || '',
      unit: p.unit || '',
      confidence: p.confidence || '',
      notes: p.notes || '',
    })
    setEditModal(p)
  }

  const handleSave = async () => {
    if (!editModal) return
    setSubmitting(true)
    try {
      await supabaseAdmin!.rpc('review_update_param', {
        p_id: editModal.id,
        p_name: editForm.name || null,
        p_value_scalar: editForm.value_scalar ? Number(editForm.value_scalar) : null,
        p_value_min: editForm.value_min ? Number(editForm.value_min) : null,
        p_value_max: editForm.value_max ? Number(editForm.value_max) : null,
        p_value_expr: editForm.value_expr || null,
        p_value_text: editForm.value_text || null,
        p_unit: editForm.unit || null,
        p_confidence: editForm.confidence || null,
        p_notes: editForm.notes || null,
        p_review_status: 'approved',
        p_reviewer: profile?.username || 'admin',
      })
      setEditModal(null)
      fetchParams()
    } catch (e) {
      console.error(e)
    } finally {
      setSubmitting(false)
    }
  }

  const toggleSelect = (id: string) => {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  const toggleAll = () => {
    if (selected.size === params.length) setSelected(new Set())
    else setSelected(new Set(params.map(p => p.id)))
  }

  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.push('/admin/review')} className="text-gray-400 hover:text-white transition-colors">
            ← 返回
          </button>
          <h1 className="text-xl font-bold">参数校对</h1>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-4">
          {([
            { id: 'needs_data', label: '缺数据' },
            { id: 'needs_review', label: '需人工审' },
            { id: 'all', label: '全部' },
          ] as const).map(tab => (
            <button
              key={tab.id}
              onClick={() => { setActiveTab(tab.id); setPage(1) }}
              className={`px-4 py-2 rounded-lg text-sm transition-colors ${
                activeTab === tab.id
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:bg-gray-800'
              }`}
            >
              {tab.label}
              {tabCounts[tab.id] != null && (
                <span className="ml-1 text-xs text-gray-500">({tabCounts[tab.id]})</span>
              )}
            </button>
          ))}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-3 mb-4">
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
            placeholder="搜索文献来源..."
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-48 focus:outline-none focus:border-gray-500"
          />
          <select
            value={filterType}
            onChange={e => { setFilterType(e.target.value); setPage(1) }}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
          >
            <option value="">全部类型</option>
            {Object.entries(VALUE_TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <select
            value={filterMaterial}
            onChange={e => { setFilterMaterial(e.target.value); setPage(1) }}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
          >
            <option value="">全部材料</option>
            {Object.entries({
              'U-10Mo': 'U-10Mo', 'U-Pu-Zr': 'U-Pu-Zr', 'UO2': 'UO₂',
              'U-Zr': 'U-Zr', 'U-Mo': 'U-Mo', 'α-U': 'α-U',
              'Zircaloy': 'Zircaloy', 'water': 'water',
            }).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>

        {/* Bulk Actions */}
        {selected.size > 0 && (
          <div className="flex items-center gap-3 mb-4 p-3 bg-gray-900 rounded-lg">
            <span className="text-sm text-gray-400">已选 {selected.size} 条</span>
            <select
              value={bulkAction || ''}
              onChange={e => setBulkAction(e.target.value as ReviewStatus)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
            >
              <option value="">批量操作...</option>
              <option value="approved">通过</option>
              <option value="rejected">拒绝</option>
              <option value="needs_data">标记缺数据</option>
              <option value="duplicate">标记重复</option>
            </select>
            <button
              onClick={handleBulkAction}
              disabled={!bulkAction || submitting}
              className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-sm disabled:opacity-50 transition-colors"
            >
              {submitting ? '执行中...' : '执行'}
            </button>
            <button onClick={() => setSelected(new Set())} className="text-sm text-gray-500 hover:text-gray-300">
              取消选择
            </button>
          </div>
        )}

        {/* Table */}
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase">
                  <th className="px-3 py-3 text-left w-8">
                    <input type="checkbox" checked={params.length > 0 && selected.size === params.length} onChange={toggleAll} />
                  </th>
                  <th className="px-3 py-3 text-left">参数名</th>
                  <th className="px-3 py-3 text-left">类型</th>
                  <th className="px-3 py-3 text-left">值</th>
                  <th className="px-3 py-3 text-left">材料</th>
                  <th className="px-3 py-3 text-left">置信度</th>
                  <th className="px-3 py-3 text-left">来源</th>
                  <th className="px-3 py-3 text-left">状态</th>
                  <th className="px-3 py-3 text-left w-24">操作</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  Array.from({ length: 10 }).map((_, i) => (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td colSpan={9} className="px-3 py-4"><div className="h-4 bg-gray-800 rounded animate-pulse" /></td>
                    </tr>
                  ))
                ) : params.length === 0 ? (
                  <tr><td colSpan={9} className="px-3 py-8 text-center text-gray-500">无匹配记录</td></tr>
                ) : params.map(p => (
                  <tr
                    key={p.id}
                    className={`border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors ${
                      selected.has(p.id) ? 'bg-gray-800/50' : ''
                    }`}
                  >
                    <td className="px-3 py-2">
                      <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggleSelect(p.id)} />
                    </td>
                    <td className="px-3 py-2 max-w-[200px]">
                      <p className="truncate font-medium">{p.name}</p>
                      {p.name_zh && <p className="text-xs text-gray-500 truncate">{p.name_zh}</p>}
                      {/* Literature summary card */}
                      {(p as any).literature && (p as any).literature.length > 0 && (
                        <div className="mt-1 text-xs text-gray-500 space-y-0.5">
                          {(p as any).literature.map((lit: LiteratureRef, i: number) => (
                            <div key={i} className="flex items-start gap-1">
                              <span className="text-blue-500 shrink-0">📄</span>
                              <div className="min-w-0">
                                <p className="truncate" title={formatLiterature(lit)}>
                                  {lit.authors && <span>{lit.authors}</span>}
                                  {lit.year && <span> ({lit.year})</span>}
                                </p>
                                <p className="truncate text-gray-600" title={lit.title}>{lit.title?.replace(/_\S+$/, '').slice(0, 50)}</p>
                              </div>
                              {/* Zotero deep link */}
                              <a
                                href={`zotero://select/items/${lit.id}`}
                                className="text-blue-400 hover:text-blue-300 shrink-0"
                                title="在 Zotero 中打开"
                                onClick={e => e.stopPropagation()}
                              >
                                Z
                              </a>
                              {/* DOI link */}
                              {lit.doi && (
                                <a
                                  href={`https://doi.org/${lit.doi}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-green-400 hover:text-green-300 shrink-0"
                                  title={lit.doi}
                                  onClick={e => e.stopPropagation()}
                                >
                                  DOI
                                </a>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-400">{VALUE_TYPE_LABELS[p.value_type] || p.value_type}</td>
                    <td className="px-3 py-2">
                      <span className="font-mono text-xs">{valueDisplay(p)}</span>
                      {p.unit && <span className="text-gray-500 ml-1">{p.unit}</span>}
                    </td>
                    <td className="px-3 py-2 text-gray-400">{p.material_raw || '—'}</td>
                    <td className="px-3 py-2">
                      {p.confidence && CONFIDENCE_LABELS[p.confidence] && (
                        <span className={`text-xs ${CONFIDENCE_LABELS[p.confidence].color}`}>
                          {CONFIDENCE_LABELS[p.confidence].label}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 max-w-[150px]">
                      <p className="text-xs text-gray-500 truncate" title={p.source_file || ''}>
                        {p.source_file ? p.source_file.replace(/^summaries\//, '').replace(/^raw\/mineru\//, '') : '—'}
                      </p>
                    </td>
                    <td className="px-3 py-2">{statusBadge(p.review_status)}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        <button
                          onClick={() => openPdf(p)}
                          className="px-2 py-1 text-xs bg-cyan-900/50 hover:bg-cyan-800/50 text-cyan-400 rounded transition-colors"
                          title="查看 PDF"
                        >
                          📑
                        </button>
                        <button
                          onClick={() => fetchTextContext(p)}
                          className="px-2 py-1 text-xs bg-purple-900/50 hover:bg-purple-800/50 text-purple-400 rounded transition-colors"
                          title="查看原文文本"
                        >
                          📖
                        </button>
                        <button
                          onClick={() => openEdit(p)}
                          className="px-2 py-1 text-xs bg-gray-800 hover:bg-gray-700 rounded transition-colors"
                          title="编辑"
                        >
                          ✏️
                        </button>
                        <button
                          onClick={async () => {
                            await supabaseAdmin!.rpc('review_batch_update', {
                              p_ids: [p.id], p_status: 'approved', p_reviewer: profile?.username || 'admin'
                            })
                            fetchParams()
                          }}
                          className="px-2 py-1 text-xs bg-green-900/50 hover:bg-green-800/50 text-green-400 rounded transition-colors"
                          title="通过"
                        >
                          ✓
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
            <span className="text-sm text-gray-500">
              {totalCount} 条，第 {page}/{totalPages || 1} 页
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="px-3 py-1 bg-gray-800 rounded text-sm disabled:opacity-30"
              >
                上一页
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-3 py-1 bg-gray-800 rounded text-sm disabled:opacity-30"
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* PDF Viewer Panel (T4b) */}
      {pdfPanel && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setPdfPanel(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-5xl h-[85vh] flex flex-col"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <h2 className="text-sm font-bold">PDF — {pdfPanel.param.name}</h2>
              <button onClick={() => setPdfPanel(null)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            <div className="flex-1 overflow-hidden">
              <iframe
                src={pdfPanel.url}
                className="w-full h-full border-0"
                title="PDF Viewer"
              />
            </div>
          </div>
        </div>
      )}

      {/* Text Context Panel (T4a) */}
      {contextPanel && (
        <div className="fixed inset-0 bg-black/60 flex items-end justify-center z-50" onClick={() => setContextPanel(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-t-xl p-6 w-full max-w-4xl max-h-[60vh] overflow-y-auto"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold">原文文本片段 — {contextPanel.param.name}</h2>
              <button onClick={() => setContextPanel(null)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            <p className="text-xs text-gray-500 mb-3">来源: {contextPanel.param.source_file}</p>
            {contextPanel.loading ? (
              <div className="h-32 bg-gray-800 rounded animate-pulse" />
            ) : (
              <pre className="whitespace-pre-wrap text-sm text-gray-300 bg-gray-800 rounded-lg p-4 font-sans">
                {contextPanel.text}
              </pre>
            )}
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setEditModal(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
            onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-4">编辑参数</h2>

            <div className="space-y-3">
              <Field label="参数名" value={editForm.name} onChange={v => setEditForm({ ...editForm, name: v })} />
              <div className="grid grid-cols-3 gap-3">
                <Field label="值 (scalar)" value={editForm.value_scalar} onChange={v => setEditForm({ ...editForm, value_scalar: v })} />
                <Field label="min" value={editForm.value_min} onChange={v => setEditForm({ ...editForm, value_min: v })} />
                <Field label="max" value={editForm.value_max} onChange={v => setEditForm({ ...editForm, value_max: v })} />
              </div>
              <Field label="表达式" value={editForm.value_expr} onChange={v => setEditForm({ ...editForm, value_expr: v })} />
              <Field label="文本" value={editForm.value_text} onChange={v => setEditForm({ ...editForm, value_text: v })} textarea />
              <Field label="单位" value={editForm.unit} onChange={v => setEditForm({ ...editForm, unit: v })} />
              <div>
                <label className="text-xs text-gray-500">置信度</label>
                <select
                  value={editForm.confidence}
                  onChange={e => setEditForm({ ...editForm, confidence: e.target.value })}
                  className="w-full mt-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
                >
                  <option value="">—</option>
                  <option value="high">高</option>
                  <option value="medium">中</option>
                  <option value="low">低</option>
                </select>
              </div>
              <Field label="备注" value={editForm.notes} onChange={v => setEditForm({ ...editForm, notes: v })} textarea />
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setEditModal(null)}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                disabled={submitting}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 rounded-lg text-sm disabled:opacity-50"
              >
                {submitting ? '保存中...' : '保存并通过'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ label, value, onChange, textarea }: {
  label: string; value: string; onChange: (v: string) => void; textarea?: boolean
}) {
  const cls = "w-full mt-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-gray-500"
  return (
    <div>
      <label className="text-xs text-gray-500">{label}</label>
      {textarea ? (
        <textarea value={value} onChange={e => onChange(e.target.value)} className={cls} rows={2} />
      ) : (
        <input value={value} onChange={e => onChange(e.target.value)} className={cls} />
      )}
    </div>
  )
}
