'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

// ─── Types ────────────────────────────────────────────────────────────────────

interface ReferenceValue {
  id: string
  element_system: string
  phase: string | null
  property: string
  value: number
  unit: string | null
  uncertainty: number | null
  temperature: number | null
  pressure: number
  source: string | null
  source_doi: string | null
  method: string | null
  created_at: string | null
  updated_at: string | null
  confidence: string | null
  needs_review: boolean | null
  cache_level: string | null
  status: string | null
  review_notes: string | null
}

interface FormData {
  element_system: string
  phase: string
  property: string
  value: string
  unit: string
  uncertainty: string
  temperature: string
  source: string
  source_doi: string
  method: string
}

type TabId = 'list' | 'review' | 'matrix'

// ─── Constants ────────────────────────────────────────────────────────────────

// Use Next.js BFF routes instead of direct external API calls
// BFF routes proxy to autovc service server-side (avoids CORS + handles downtime)

const PROPERTY_LABELS: Record<string, string> = {
  lattice_constant: '晶格常数',
  cohesive_energy: '结合能',
  bulk_modulus: '体积模量',
  elastic_constants: '弹性常数',
  shear_modulus: '剪切模量',
  C11: '弹性常数C11',
  C12: '弹性常数C12',
  C44: '弹性常数C44',
  vacancy_formation_energy: '空位形成能',
  surface_energy: '表面能',
}

const PHASE_LABELS: Record<string, string> = {
  BCC: '体心立方',
  FCC: '面心立方',
  HCP: '密排六方',
}

function propLabel(key: string): string {
  return PROPERTY_LABELS[key] || key
}

function phaseLabel(key: string | null): string {
  if (!key) return '—'
  return PHASE_LABELS[key] || key
}

const STATUS_BADGE: Record<string, { cls: string; label: string }> = {
  approved: { cls: 'bg-green-900/50 text-green-300', label: '已通过' },
  rejected: { cls: 'bg-red-900/50 text-red-300', label: '已拒绝' },
  pending: { cls: 'bg-yellow-900/50 text-yellow-300', label: '待审核' },
}

function statusBadge(status: string | null) {
  if (!status) return null
  const info = STATUS_BADGE[status] || { cls: 'bg-gray-700 text-gray-300', label: status }
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${info.cls}`}>{info.label}</span>
  )
}

const EMPTY_FORM: FormData = {
  element_system: '',
  phase: '',
  property: '',
  value: '',
  unit: '',
  uncertainty: '',
  temperature: '',
  source: '',
  source_doi: '',
  method: '',
}

const PAGE_SIZE = 20

// ─── Main Component ────────────────────────────────────────────────────────────

export default function AdminReferencesPage() {
  const router = useRouter()
  const { user, loading } = useAuth()

  // Auth guard
  useEffect(() => {
    if (!loading) {
      if (!user) router.push('/login')
      else if (user.blog_role !== "admin") router.push('/')
    }
  }, [loading, user, router])

  // Tab state
  const [activeTab, setActiveTab] = useState<TabId>('list')

  // ─── Tab 1: List state ───────────────────────────────────────────────────
  const [refs, setRefs] = useState<ReferenceValue[]>([])
  const [loadingRefs, setLoadingRefs] = useState(true)
  const [refsError, setRefsError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filterSystem, setFilterSystem] = useState('')
  const [filterProperty, setFilterProperty] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [page, setPage] = useState(1)

  // Modal
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRef, setEditingRef] = useState<ReferenceValue | null>(null)
  const [form, setForm] = useState<FormData>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<ReferenceValue | null>(null)

  // ─── Tab 2: Review state ─────────────────────────────────────────────────
  const [reviewItems, setReviewItems] = useState<ReferenceValue[]>([])
  const [loadingReview, setLoadingReview] = useState(false)
  const [selectedReviewIds, setSelectedReviewIds] = useState<Set<string>>(new Set())
  const [reviewAction, setReviewAction] = useState<{ type: 'approve' | 'reject'; notes?: string } | null>(null)
  const [submittingReview, setSubmittingReview] = useState(false)

  // ─── Tab 3: Matrix state ─────────────────────────────────────────────────
  const [matrixData, setMatrixData] = useState<{ systems: string[]; properties: string[]; cells: Record<string, Record<string, unknown>> } | null>(null)
  const [loadingMatrix, setLoadingMatrix] = useState(false)

  // ─── Data fetching ───────────────────────────────────────────────────────
  // Note: After Supabase migration, reference values are managed via the
  // backend API (/api/v1/reference-values/*).  The old Next.js BFF routes
  // (/api/admin/ref-values/*) are no longer functional in production.

  const fetchRefs = useCallback(async () => {
    setLoadingRefs(true)
    setRefsError(null)
    try {
      // Use backend export endpoint (requires credentials for auth)
      const r = await fetch(`/api/v1/reference-values/export?format=json`, {
        credentials: 'include',
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      const raw = Array.isArray(data) ? data : data?.data ?? []
      // Map backend field names to frontend ReferenceValue shape
      const mapped = raw.map((item: any) => ({
        id: item.staging_id || item.id || '',
        element_system: item.element_system || '',
        phase: item.phase || null,
        property: item.property_name || item.property || '',
        value: typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0,
        unit: item.unit || null,
        uncertainty: item.uncertainty ?? null,
        temperature: item.temperature ?? null,
        pressure: item.pressure ?? 0,
        source: item.source || item.source_doi || null,
        source_doi: item.source_doi || null,
        method: item.method || null,
        created_at: item.created_at || null,
        updated_at: item.updated_at || null,
        confidence: item.confidence || null,
        needs_review: item.needs_review ?? null,
        status: item.status || 'approved',
        has_reference: item.has_reference ?? true,
        review_notes: item.review_notes || null,
      }))
      setRefs(mapped)
    } catch (e: any) {
      setRefsError(e.message || '加载参考值失败')
    } finally {
      setLoadingRefs(false)
    }
  }, [])

  const fetchReviewItems = useCallback(async () => {
    setLoadingReview(true)
    try {
      const r = await fetch(`/api/v1/reference-values/pending-review?per_page=100`, {
        credentials: 'include',
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      // Backend returns { success, data: { records, total, ... } }
      setReviewItems(data?.data?.records ?? Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [])
    } catch {
      // fallback: filter from main list
      const pending = refs.filter(r => r.status === 'pending' || r.needs_review === true)
      setReviewItems(pending)
    } finally {
      setLoadingReview(false)
    }
  }, [refs])

  const fetchMatrix = useCallback(async () => {
    setLoadingMatrix(true)
    try {
      const r = await fetch(`/api/v1/reference-values/export?format=json`, {
        credentials: 'include',
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      const items: any[] = Array.isArray(data) ? data : data?.data ?? []
      // Build matrix from exported data
      const systemSet = new Set<string>()
      const propSet = new Set<string>()
      items.forEach((r: any) => {
        if (r.element_system) systemSet.add(String(r.element_system))
        if (r.property_name) propSet.add(String(r.property_name))
      })
      const systems = Array.from(systemSet).sort()
      const properties = Array.from(propSet).sort()
      const cells: Record<string, Record<string, unknown>> = {}
      for (const s of systems) {
        const row: Record<string, unknown> = {}
        for (const p of properties) {
          row[p] = null
        }
        cells[s] = row
      }
      items.forEach((r: any) => {
        const sys = cells[r.element_system]
        if (sys) sys[r.property_name] = r.value
      })
      setMatrixData({ systems, properties, cells })
    } catch {
      setMatrixData(null)
    } finally {
      setLoadingMatrix(false)
    }
  }, [])

  useEffect(() => {
    if (user?.blog_role === "admin") fetchRefs()
  },  [user, fetchRefs])

  useEffect(() => {
    if (activeTab === 'review') fetchReviewItems()
  }, [activeTab, fetchReviewItems])

  useEffect(() => {
    if (activeTab === 'matrix') fetchMatrix()
  }, [activeTab, fetchMatrix])

  // ─── Filtering & Pagination ─────────────────────────────────────────────

  const filteredRefs = refs.filter(r => {
    if (filterSystem && r.element_system !== filterSystem) return false
    if (filterProperty && r.property !== filterProperty) return false
    if (filterStatus && r.status !== filterStatus) return false
    if (search) {
      const q = search.toLowerCase()
      const searchable = `${r.element_system} ${r.property} ${r.source} ${r.source_doi || ''} ${r.method || ''}`.toLowerCase()
      if (!searchable.includes(q)) return false
    }
    return true
  })

  const uniqueSystems = [...new Set(refs.map(r => r.element_system))].sort()
  const uniqueProperties = [...new Set(refs.map(r => r.property))].sort()
  const uniqueStatuses = [...new Set(refs.map(r => r.status).filter(Boolean) as string[])].sort()

  const totalPages = Math.ceil(filteredRefs.length / PAGE_SIZE)
  const pagedRefs = filteredRefs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // Reset page on filter change
  useEffect(() => { setPage(1) }, [filterSystem, filterProperty, filterStatus, search])

  // ─── CRUD Handlers ──────────────────────────────────────────────────────

  const openCreate = () => {
    setEditingRef(null)
    setForm(EMPTY_FORM)
    setModalOpen(true)
  }

  const openEdit = (r: ReferenceValue) => {
    setEditingRef(r)
    setForm({
      element_system: r.element_system,
      phase: r.phase || '',
      property: r.property,
      value: String(r.value),
      unit: r.unit || '',
      uncertainty: r.uncertainty != null ? String(r.uncertainty) : '',
      temperature: r.temperature != null ? String(r.temperature) : '',
      source: r.source || '',
      source_doi: r.source_doi || '',
      method: r.method || '',
    })
    setModalOpen(true)
  }

  const handleSave = async () => {
    if (!form.element_system || !form.property || !form.value) {
      setRefsError('请填写必填字段')
      return
    }
    setSubmitting(true)
    setRefsError(null)

    try {
      const body: Record<string, unknown> = {
        element_system: form.element_system,
        phase: form.phase || null,
        property: form.property,
        value: parseFloat(form.value),
        unit: form.unit || null,
        uncertainty: form.uncertainty ? parseFloat(form.uncertainty) : null,
        temperature: form.temperature ? parseFloat(form.temperature) : null,
        source: form.source || null,
        source_doi: form.source_doi || null,
        method: form.method || null,
      }

      if (editingRef) {
        // PATCH not available in backend API — show error
        setRefsError('编辑功能暂不可用（后端 API 迁移中）')
      } else {
        // POST create via bulk endpoint
        const r = await fetch(`/api/v1/reference-values/bulk`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': '' },
          credentials: 'include',
          body: JSON.stringify({ values: [body] }),
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
      }

      setModalOpen(false)
      await fetchRefs()
    } catch (e: any) {
      setRefsError(e.message || '保存失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setRefsError('删除功能暂不可用（后端 API 迁移中）')
    setDeleteTarget(null)
  }

  // ─── Review Handlers ─────────────────────────────────────────────────────

  const handleSingleReview = async (id: string, action: 'approve' | 'reject', notes?: string) => {
    setSubmittingReview(true)
    try {
      const body2: Record<string, unknown> = action === 'approve'
        ? { review_note: notes }
        : { reason: notes }
      const r = await fetch(`/api/v1/reference-values/${id}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body2),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      await fetchReviewItems()
      await fetchRefs()
    } catch (e: any) {
      setRefsError(e.message || `${action} 失败`)
    } finally {
      setSubmittingReview(false)
    }
  }

  const handleBatchReview = async () => {
    if (!reviewAction || selectedReviewIds.size === 0) return
    setSubmittingReview(true)
    try {
      const ids = Array.from(selectedReviewIds)
      const body: Record<string, unknown> = {
        ids,
        action: reviewAction.type,
      }
      if (reviewAction.notes) body[reviewAction.type === 'approve' ? 'review_notes' : 'reason'] = reviewAction.notes

      const r = await fetch(`/api/v1/reference-values/bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          action: reviewAction.type,
          staging_ids: ids,
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)

      setSelectedReviewIds(new Set())
      setReviewAction(null)
      await fetchReviewItems()
      await fetchRefs()
    } catch (e: any) {
      setRefsError(e.message || '批量操作失败')
    } finally {
      setSubmittingReview(false)
    }
  }

  // ─── Render Helpers ──────────────────────────────────────────────────────

  if (loading || !user) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (user.blog_role !== "admin") return null

  const TABS: { id: TabId; label: string }[] = [
    { id: 'list', label: '参考值列表' },
    { id: 'review', label: '审核队列' },
    { id: 'matrix', label: '覆盖矩阵' },
  ]

  const renderHeader = () => (
    <div className="mb-6 flex items-center justify-between">
      <div>
        <h1 className="text-2xl font-bold text-white">参考值管理</h1>
        <p className="text-gray-400 text-sm mt-1">管理势函数验证参考数据</p>
      </div>
      <a
        href="/admin"
        className="text-sm text-gray-400 hover:text-white transition px-3 py-1.5 border border-gray-700 rounded-lg"
      >
        ← 返回管理后台
      </a>
    </div>
  )

  const renderTabs = () => (
    <div className="mb-6 flex items-center gap-1 border-b border-gray-700 pb-px">
      {TABS.map(t => (
        <button
          key={t.id}
          onClick={() => setActiveTab(t.id)}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
            activeTab === t.id
              ? 'border-blue-500 text-white'
              : 'border-transparent text-gray-400 hover:text-gray-200'
          }`}
        >
          {t.label}
          {t.id === 'review' && reviewItems.length > 0 && (
            <span className="ml-1.5 text-xs bg-yellow-600 text-white px-1.5 py-0.5 rounded-full">
              {reviewItems.length}
            </span>
          )}
        </button>
      ))}
      <div className="ml-auto">
        <button
          onClick={openCreate}
          className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition"
        >
          + 新增参考值
        </button>
      </div>
    </div>
  )

  const renderError = () => {
    if (!refsError) return null
    return (
      <div className="mb-4 px-4 py-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
        {refsError}
        <button onClick={() => setRefsError(null)} className="ml-2 text-red-400 hover:text-red-300">✕</button>
      </div>
    )
  }

  // ─── Tab 1: Reference List ──────────────────────────────────────────────

  const renderListTab = () => (
    <>
      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          value={filterSystem}
          onChange={e => setFilterSystem(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          <option value="">所有体系</option>
          {uniqueSystems.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={filterProperty}
          onChange={e => setFilterProperty(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          <option value="">所有属性</option>
          {uniqueProperties.map(p => (
            <option key={p} value={p}>{propLabel(p)}</option>
          ))}
        </select>
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          <option value="">所有状态</option>
          {uniqueStatuses.map(s => (
            <option key={s} value={s}>{STATUS_BADGE[s]?.label || s}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="搜索..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-48"
        />
        <span className="text-xs text-gray-500">{filteredRefs.length} 条记录</span>
      </div>

      {/* Table */}
      {loadingRefs ? (
        <div className="text-gray-500 py-8 text-center">加载参考值...</div>
      ) : (
        <>
          <div className="bg-gray-800/50 rounded-xl border border-gray-700 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-700">
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">体系</th>
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">相</th>
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">属性</th>
                  <th className="px-3 py-2 text-right text-gray-300 whitespace-nowrap">值</th>
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">单位</th>
                  <th className="px-3 py-2 text-right text-gray-300 whitespace-nowrap">不确定度</th>
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">来源</th>
                  <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">DOI</th>
                  <th className="px-3 py-2 text-center text-gray-300 whitespace-nowrap">状态</th>
                  <th className="px-3 py-2 text-center text-gray-300 whitespace-nowrap">操作</th>
                </tr>
              </thead>
              <tbody>
                {pagedRefs.map(r => (
                  <tr key={r.id} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition cursor-pointer">
                    <td className="px-3 py-2 font-medium text-white whitespace-nowrap">{r.element_system}</td>
                    <td className="px-3 py-2 text-gray-300 whitespace-nowrap">{phaseLabel(r.phase)}</td>
                    <td className="px-3 py-2 text-gray-300 whitespace-nowrap">{propLabel(r.property)}</td>
                    <td className="px-3 py-2 text-right font-mono text-white whitespace-nowrap">{r.value?.toPrecision(6)}</td>
                    <td className="px-3 py-2 text-gray-400 whitespace-nowrap">{r.unit || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono text-gray-400 whitespace-nowrap">{r.uncertainty != null ? r.uncertainty.toPrecision(4) : '—'}</td>
                    <td className="px-3 py-2 text-gray-400 max-w-[200px] truncate" title={r.source || undefined}>{r.source || '—'}</td>
                    <td className="px-3 py-2 text-gray-400 max-w-[120px] truncate" title={r.source_doi || undefined}>
                      {r.source_doi ? (
                        <a href={`https://doi.org/${r.source_doi}`} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:text-blue-300" onClick={e => e.stopPropagation()}>
                          {r.source_doi}
                        </a>
                      ) : '—'}
                    </td>
                    <td className="px-3 py-2 text-center whitespace-nowrap">{statusBadge(r.status)}</td>
                    <td className="px-3 py-2 text-center whitespace-nowrap">
                      <button onClick={() => openEdit(r)} className="text-blue-400 hover:text-blue-300 text-xs mr-2">编辑</button>
                      <button onClick={() => setDeleteTarget(r)} className="text-red-400 hover:text-red-300 text-xs">删除</button>
                    </td>
                  </tr>
                ))}
                {pagedRefs.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-gray-500">无匹配参考值</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 text-sm rounded-lg border border-gray-700 text-gray-300 hover:text-white disabled:opacity-40 transition"
              >
                ← 上一页
              </button>
              <span className="text-sm text-gray-400">{page} / {totalPages}</span>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1 text-sm rounded-lg border border-gray-700 text-gray-300 hover:text-white disabled:opacity-40 transition"
              >
                下一页 →
              </button>
            </div>
          )}
        </>
      )}
    </>
  )

  // ─── Tab 2: Review Queue ────────────────────────────────────────────────

  const renderReviewTab = () => (
    <>
      {/* Batch actions */}
      {selectedReviewIds.size > 0 && (
        <div className="mb-4 flex items-center gap-3 px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg">
          <span className="text-sm text-gray-300">已选 {selectedReviewIds.size} 条</span>
          <button
            onClick={() => setReviewAction({ type: 'approve', notes: '' })}
            className="px-3 py-1 text-sm rounded-lg bg-green-600 hover:bg-green-500 text-white transition"
          >
            批量通过
          </button>
          <button
            onClick={() => setReviewAction({ type: 'reject', notes: '' })}
            className="px-3 py-1 text-sm rounded-lg bg-red-600 hover:bg-red-500 text-white transition"
          >
            批量拒绝
          </button>
          <button
            onClick={() => setSelectedReviewIds(new Set())}
            className="px-3 py-1 text-sm rounded-lg border border-gray-600 text-gray-300 hover:text-white transition"
          >
            取消选择
          </button>
        </div>
      )}

      {loadingReview ? (
        <div className="text-gray-500 py-8 text-center">加载审核队列...</div>
      ) : reviewItems.length === 0 ? (
        <div className="text-gray-500 py-8 text-center">暂无待审核条目</div>
      ) : (
        <div className="bg-gray-800/50 rounded-xl border border-gray-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-700">
                <th className="px-3 py-2 text-center text-gray-300 w-10">
                  <input
                    type="checkbox"
                    checked={selectedReviewIds.size === reviewItems.length && reviewItems.length > 0}
                    onChange={() => {
                      if (selectedReviewIds.size === reviewItems.length) setSelectedReviewIds(new Set())
                      else setSelectedReviewIds(new Set(reviewItems.map(r => r.id)))
                    }}
                    className="rounded"
                  />
                </th>
                <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">体系</th>
                <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">相</th>
                <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">属性</th>
                <th className="px-3 py-2 text-right text-gray-300 whitespace-nowrap">值</th>
                <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">单位</th>
                <th className="px-3 py-2 text-left text-gray-300 whitespace-nowrap">来源</th>
                <th className="px-3 py-2 text-center text-gray-300 whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody>
              {reviewItems.map(r => (
                <tr key={r.id} className={`border-b border-gray-700/50 transition ${selectedReviewIds.has(r.id) ? 'bg-blue-900/20' : 'hover:bg-gray-700/30'}`}>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={selectedReviewIds.has(r.id)}
                      onChange={() => {
                        const next = new Set(selectedReviewIds)
                        if (next.has(r.id)) next.delete(r.id)
                        else next.add(r.id)
                        setSelectedReviewIds(next)
                      }}
                      className="rounded"
                    />
                  </td>
                  <td className="px-3 py-2 font-medium text-white whitespace-nowrap">{r.element_system}</td>
                  <td className="px-3 py-2 text-gray-300 whitespace-nowrap">{phaseLabel(r.phase)}</td>
                  <td className="px-3 py-2 text-gray-300 whitespace-nowrap">{propLabel(r.property)}</td>
                  <td className="px-3 py-2 text-right font-mono text-white whitespace-nowrap">{r.value?.toPrecision(6)}</td>
                  <td className="px-3 py-2 text-gray-400 whitespace-nowrap">{r.unit || '—'}</td>
                  <td className="px-3 py-2 text-gray-400 max-w-[200px] truncate" title={r.source || undefined}>{r.source || '—'}</td>
                  <td className="px-3 py-2 text-center whitespace-nowrap">
                    <button
                      onClick={() => handleSingleReview(r.id, 'approve')}
                      disabled={submittingReview}
                      className="text-green-400 hover:text-green-300 text-xs mr-2 disabled:opacity-50"
                    >
                      通过
                    </button>
                    <button
                      onClick={() => handleSingleReview(r.id, 'reject')}
                      disabled={submittingReview}
                      className="text-red-400 hover:text-red-300 text-xs disabled:opacity-50"
                    >
                      拒绝
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Batch action modal */}
      {reviewAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-800 border border-gray-600 rounded-xl p-6 w-full max-w-md mx-4 shadow-2xl">
            <h2 className="text-lg font-semibold text-white mb-4">
              {reviewAction.type === 'approve' ? '批量通过' : '批量拒绝'} ({selectedReviewIds.size} 条)
            </h2>
            <textarea
              placeholder={reviewAction.type === 'approve' ? '审核备注（可选）' : '拒绝原因（可选）'}
              value={reviewAction.notes || ''}
              onChange={e => setReviewAction({ ...reviewAction, notes: e.target.value })}
              className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none h-24"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => setReviewAction(null)}
                className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-300 hover:text-white transition"
              >
                取消
              </button>
              <button
                onClick={handleBatchReview}
                disabled={submittingReview}
                className={`px-4 py-2 text-sm rounded-lg text-white transition disabled:opacity-50 ${
                  reviewAction.type === 'approve' ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'
                }`}
              >
                {submittingReview ? '处理中...' : '确认'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )

  // ─── Tab 3: Coverage Matrix ─────────────────────────────────────────────

  const renderMatrixTab = () => {
    if (loadingMatrix) {
      return <div className="text-gray-500 py-8 text-center">加载覆盖矩阵...</div>
    }

    if (!matrixData || !matrixData.systems || !matrixData.properties) {
      return (
        <div className="text-gray-500 py-8 text-center">
          无法加载覆盖矩阵数据。请确认后端 API 是否可用。
          <button
            onClick={fetchMatrix}
            className="ml-2 text-blue-400 hover:text-blue-300 text-sm"
          >
            重试
          </button>
        </div>
      )
    }

    const { systems, properties, cells } = matrixData
    const cellKey = (s: string, p: string) => `${s}::${p}`

    // Compute coverage stats
    const propCounts: Record<string, { total: number; filled: number }> = {}
    properties.forEach(p => {
      propCounts[p] = { total: systems.length, filled: 0 }
      systems.forEach(s => {
        if (cells?.[cellKey(s, p)]) (propCounts[p] as { total: number; filled: number }).filled++
      })
    })

    return (
      <div className="bg-gray-800/50 rounded-xl border border-gray-700 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-700">
              <th className="px-3 py-2 text-left text-gray-300 sticky left-0 bg-gray-700 z-10">体系</th>
              <th className="px-3 py-2 text-center text-gray-300">覆盖率</th>
              {properties.map(p => (
                <th key={p} className="px-2 py-2 text-center text-gray-300 whitespace-nowrap" title={p}>
                  <div className="flex flex-col items-center gap-0.5">
                    <span>{propLabel(p)}</span>
                    <span className="text-[10px] text-gray-500">{propCounts[p]?.filled ?? 0}/{propCounts[p]?.total ?? 0}</span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {systems.map(s => {
              let rowFilled = 0
              properties.forEach(p => { if (cells?.[cellKey(s, p)]) rowFilled++ })

              return (
                <tr key={s} className="border-b border-gray-700/50">
                  <td className="px-3 py-2 font-medium text-white sticky left-0 bg-gray-800 z-10 whitespace-nowrap">{s}</td>
                  <td className="px-3 py-2 text-center text-gray-400 whitespace-nowrap">
                    {rowFilled}/{properties.length} ({((rowFilled / properties.length) * 100).toFixed(0)}%)
                  </td>
                  {properties.map(p => {
                    const data = cells?.[cellKey(s, p)]
                    if (data) {
                      const val = typeof data === 'object' ? (data as Record<string, unknown>).value : data
                      const conf = typeof data === 'object' ? (data as Record<string, unknown>).confidence : null
                      const colorClass = conf === 'A' ? 'bg-green-900/40 text-green-300'
                        : conf === 'B' ? 'bg-blue-900/40 text-blue-300'
                        : conf === 'C' ? 'bg-yellow-900/40 text-yellow-300'
                        : 'bg-green-900/30 text-green-400'
                      return (
                        <td key={p} className={`px-2 py-2 text-center font-mono whitespace-nowrap ${colorClass}`}>
                          {typeof val === 'number' ? val.toPrecision(4) : String(val)}
                        </td>
                      )
                    }
                    return (
                      <td key={p} className="px-2 py-2 text-center text-gray-600">—</td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  // ─── Main Render ──────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-7xl mx-auto px-4 py-8">
        {renderHeader()}
        {renderTabs()}
        {renderError()}

        {activeTab === 'list' && renderListTab()}
        {activeTab === 'review' && renderReviewTab()}
        {activeTab === 'matrix' && renderMatrixTab()}
      </div>

      {/* ─── Create/Edit Modal ─────────────────────────────────────────── */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-800 border border-gray-600 rounded-xl p-6 w-full max-w-lg mx-4 shadow-2xl max-h-[90vh] overflow-y-auto">
            <h2 className="text-lg font-semibold text-white mb-4">
              {editingRef ? '编辑参考值' : '新增参考值'}
            </h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">体系 *</label>
                <input
                  value={form.element_system}
                  onChange={e => setForm({ ...form, element_system: e.target.value })}
                  placeholder="e.g. U, U-Zr"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">相</label>
                <select
                  value={form.phase}
                  onChange={e => setForm({ ...form, phase: e.target.value })}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="">—</option>
                  <option value="BCC">BCC (体心立方)</option>
                  <option value="FCC">FCC (面心立方)</option>
                  <option value="HCP">HCP (密排六方)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">属性 *</label>
                <select
                  value={form.property}
                  onChange={e => setForm({ ...form, property: e.target.value })}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="">请选择</option>
                  {Object.entries(PROPERTY_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v} ({k})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">值 *</label>
                <input
                  type="number"
                  step="any"
                  value={form.value}
                  onChange={e => setForm({ ...form, value: e.target.value })}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">单位</label>
                <input
                  value={form.unit}
                  onChange={e => setForm({ ...form, unit: e.target.value })}
                  placeholder="e.g. Å, eV, GPa"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">不确定度</label>
                <input
                  type="number"
                  step="any"
                  value={form.uncertainty}
                  onChange={e => setForm({ ...form, uncertainty: e.target.value })}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">温度 (K)</label>
                <input
                  type="number"
                  step="any"
                  value={form.temperature}
                  onChange={e => setForm({ ...form, temperature: e.target.value })}
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">方法</label>
                <input
                  value={form.method}
                  onChange={e => setForm({ ...form, method: e.target.value })}
                  placeholder="e.g. DFT, experiment"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm text-gray-400 mb-1">来源</label>
                <input
                  value={form.source}
                  onChange={e => setForm({ ...form, source: e.target.value })}
                  placeholder="文献来源"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-sm text-gray-400 mb-1">DOI</label>
                <input
                  value={form.source_doi}
                  onChange={e => setForm({ ...form, source_doi: e.target.value })}
                  placeholder="10.xxxx/xxxxx"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => { setModalOpen(false); setRefsError(null) }}
                className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-300 hover:text-white transition"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                disabled={submitting || !form.element_system || !form.property || !form.value}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Delete Confirm Modal ─────────────────────────────────────── */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-800 border border-gray-600 rounded-xl p-6 w-full max-w-sm mx-4 shadow-2xl">
            <h2 className="text-lg font-semibold text-white mb-2">确认删除</h2>
            <p className="text-sm text-gray-400 mb-6">
              确定要删除 <span className="text-white">{deleteTarget.element_system}</span> — <span className="text-white">{propLabel(deleteTarget.property)}</span> 的参考值吗？此操作不可撤销。
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-300 hover:text-white transition"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 text-sm rounded-lg bg-red-600 hover:bg-red-500 text-white transition"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
