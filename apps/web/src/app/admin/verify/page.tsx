'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import VerificationBadge from '@/components/VerificationBadge'
import VerificationProgressBar from '@/components/VerificationProgressBar'

// ─── Types ────────────────────────────────────────────────────────────────────

interface Potential {
  id: string
  name: string
  display_name: string | null
  type: string
  elements: string[]
  verified_props: Record<string, unknown> | null
}

interface VerifyJob {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  current_step: string | null
  estimated_remaining_seconds: number | null
  results: PropertyResult[] | null
  overall_grade: string | null
}

interface PropertyResult {
  property_name: string
  computed_value: number
  reference_value: number
  unit: string
  relative_error: number
  grade: string
}

interface VerificationRecord {
  id: string
  potential_id: string
  potential_name: string
  template: string
  status: string
  overall_grade: string | null
  created_at: string
  completed_at: string | null
  results: PropertyResult[] | null
}

type Template = 'basic' | 'mechanical' | 'defect' | 'comprehensive'

const TEMPLATE_INFO: Record<Template, { label: string; desc: string }> = {
  basic: { label: '基础', desc: '晶格常数、结合能等基本性质' },
  mechanical: { label: '力学', desc: '弹性常数、体积模量等力学性质' },
  defect: { label: '缺陷', desc: '空位/间隙形成能、表面能等缺陷性质' },
  comprehensive: { label: '全面', desc: '所有可用性质的完整验证' },
}

const PROPERTY_LABELS: Record<string, string> = {
  lattice_constant: '晶格常数',
  cohesive_energy: '结合能',
  elastic_constants: '弹性常数',
  bulk_modulus: '体积模量',
  shear_modulus: '剪切模量',
  vacancy_formation_energy: '空位形成能',
  interstitial_formation_energy: '间隙形成能',
  surface_energy: '表面能',
  melting_point: '熔点',
  formation_energy: '形成能',
  thermal_expansion: '热膨胀系数',
  specific_heat: '比热容',
}

function propLabel(key: string): string {
  return PROPERTY_LABELS[key] || key
}

function gradeColor(grade: string): string {
  const c: Record<string, string> = {
    A: 'text-green-400',
    B: 'text-blue-400',
    C: 'text-yellow-400',
    D: 'text-orange-400',
    F: 'text-red-400',
  }
  return c[grade.toUpperCase()] || 'text-gray-400'
}

// Use Next.js BFF proxy routes to avoid CORS and handle upstream downtime

// ─── Main Component ───────────────────────────────────────────────────────────

export default function AdminVerifyPage() {
  const router = useRouter()
  const { user, loading } = useAuth()

  const [potentials, setPotentials] = useState<Potential[]>([])
  const [loadingPotentials, setLoadingPotentials] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Template dialog
  const [dialogPotential, setDialogPotential] = useState<Potential | null>(null)
  const [selectedTemplate, setSelectedTemplate] = useState<Template>('basic')
  const [submitting, setSubmitting] = useState(false)

  // Active job
  const [activeJob, setActiveJob] = useState<VerifyJob | null>(null)
  const [activePotentialName, setActivePotentialName] = useState<string>('')
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Verification history (in-memory for now)
  const [history, setHistory] = useState<VerificationRecord[]>([])

  // 批量验证状态
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchTemplate, setBatchTemplate] = useState<Template>('basic')
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [batchJobs, setBatchJobs] = useState<Record<string, {
    status: 'pending' | 'running' | 'completed' | 'failed'
    progress: number
    overall_grade: string | null
    potentialName: string
    error?: string
  }>>({})
  const batchPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 批量提交验证
  const handleBatchSubmit = async () => {
    if (selectedIds.size === 0) return
    setBatchSubmitting(true)
    setError(null)

    const potentialsToVerify = potentials.filter(p => selectedIds.has(p.id))
    const initialJobs: typeof batchJobs = {}
    for (const p of potentialsToVerify) {
      initialJobs[p.id] = {
        status: 'pending',
        progress: 0,
        overall_grade: null,
        potentialName: p.display_name || p.name,
      }
    }
    setBatchJobs(initialJobs)

    // 并行提交所有验证任务
    const results = await Promise.allSettled(
      potentialsToVerify.map(async (p) => {
        const r = await fetch(`/api/verify`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ potential_id: p.id, template: batchTemplate }),
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data = await r.json()
        return { potential: p, jobId: data.job_id }
      })
    )

    // 处理提交结果，成功项开始轮询
    const pollTargets: { jobId: string; potentialId: string; potentialName: string }[] = []
    const jobUpdates: typeof batchJobs = { ...initialJobs }

    results.forEach((result, i) => {
      const p = potentialsToVerify[i]!
      const existing = jobUpdates[p.id]
      if (result.status === 'fulfilled') {
        pollTargets.push({
          jobId: result.value.jobId,
          potentialId: p.id,
          potentialName: result.value.potential.display_name || result.value.potential.name,
        })
        jobUpdates[p.id] = { ...existing!, status: 'running' }
      } else {
        jobUpdates[p.id] = { ...existing!, status: 'failed', error: result.reason?.message || '提交失败' }
      }
    })
    setBatchJobs(jobUpdates)
    setBatchSubmitting(false)

    // 轮询所有成功提交的任务
    if (pollTargets.length > 0) {
      if (batchPollRef.current) clearInterval(batchPollRef.current)
      batchPollRef.current = setInterval(async () => {
        const pollResults = await Promise.allSettled(
          pollTargets.map(async ({ jobId, potentialId }) => {
            const r = await fetch(`/api/verify/${jobId}`)
            if (!r.ok) throw new Error('Poll failed')
            return { potentialId, job: await r.json() as VerifyJob }
          })
        )

        let allDone = true
        const updates: typeof batchJobs = { ...batchJobs }
        pollResults.forEach(result => {
          if (result.status === 'fulfilled') {
            const { potentialId, job } = result.value
            const existing = updates[potentialId]!
            updates[potentialId] = {
              ...existing,
              status: job.status,
              progress: job.progress,
              overall_grade: job.overall_grade,
            }
            if (job.status === 'completed') {
              setHistory(prev => [{
                id: job.job_id,
                potential_id: potentialId,
                potential_name: updates[potentialId]?.potentialName ?? '',
                template: batchTemplate,
                status: job.status,
                overall_grade: job.overall_grade,
                created_at: new Date().toISOString(),
                completed_at: new Date().toISOString(),
                results: job.results,
              }, ...prev])
            } else {
              allDone = false
            }
          }
        })
        setBatchJobs(updates)
        if (allDone) {
          if (batchPollRef.current) clearInterval(batchPollRef.current)
          batchPollRef.current = null
          setSelectedIds(new Set())
        }
      }, 3000)
    }
  }

  // 批量统计
  const batchEntries = Object.entries(batchJobs)
  const batchTotal = batchEntries.length
  const batchCompleted = batchEntries.filter(([, j]) => j.status === 'completed' || j.status === 'failed').length
  const batchGradeA = batchEntries.filter(([, j]) => j.overall_grade?.toUpperCase() === 'A').length
  const batchGradeB = batchEntries.filter(([, j]) => j.overall_grade?.toUpperCase() === 'B').length
  const hasActiveBatch = batchTotal > 0 && batchCompleted < batchTotal

  // Auth guard
  useEffect(() => {
    if (!loading) {
      if (!user) {
        router.push('/login')
      } else if (user.blog_role !== "admin") {
        router.push('/')
      }
    }
  }, [loading, user, router])

  // Load potentials list
  useEffect(() => {
    fetch('/api/potentials?limit=200')
      .then(r => r.json())
      .then(data => {
        setPotentials(data.potentials || [])
      })
      .catch(() => setError('加载势函数列表失败'))
      .finally(() => setLoadingPotentials(false))
  }, [])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
      if (batchPollRef.current) clearInterval(batchPollRef.current)
    }
  }, [])

  const filtered = potentials.filter(p => {
    if (!searchQuery) return true
    const q = searchQuery.toLowerCase()
    return (
      p.name.toLowerCase().includes(q) ||
      (p.display_name || '').toLowerCase().includes(q) ||
      p.elements.some(e => e.toLowerCase().includes(q)) ||
      p.type.toLowerCase().includes(q)
    )
  })

  // 全选/取消全选
  const allSelected = filtered.length > 0 && filtered.every(p => selectedIds.has(p.id))
  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filtered.map(p => p.id)))
    }
  }
  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Start polling for job status
  const startPolling = useCallback((jobId: string, potentialName: string) => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    setActivePotentialName(potentialName)

    pollingRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/verify/${jobId}`)
        if (!r.ok) throw new Error('Poll failed')
        const job: VerifyJob = await r.json()
        setActiveJob(job)

        if (job.status === 'completed' || job.status === 'failed') {
          if (pollingRef.current) clearInterval(pollingRef.current)
          pollingRef.current = null

          // Add to history
          setHistory(prev => [{
            id: jobId,
            potential_id: dialogPotential?.id || '',
            potential_name: potentialName,
            template: selectedTemplate,
            status: job.status,
            overall_grade: job.overall_grade,
            created_at: new Date().toISOString(),
            completed_at: new Date().toISOString(),
            results: job.results,
          }, ...prev])

          setDialogPotential(null)
        }
      } catch {
        // ignore polling errors
      }
    }, 3000)
  }, [dialogPotential?.id, selectedTemplate])

  // Submit verification
  const handleSubmit = async () => {
    if (!dialogPotential) return
    setSubmitting(true)
    setError(null)

    try {
      const r = await fetch(`/api/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          potential_id: dialogPotential.id,
          template: selectedTemplate,
        }),
      })

      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: '提交失败' }))
        throw new Error(err.error || `HTTP ${r.status}`)
      }

      const data = await r.json()
      const job: VerifyJob = {
        job_id: data.job_id,
        status: 'pending',
        progress: 0,
        current_step: null,
        estimated_remaining_seconds: data.estimated_seconds ?? null,
        results: null,
        overall_grade: null,
      }
      setActiveJob(job)
      startPolling(data.job_id, dialogPotential.display_name || dialogPotential.name)
      setDialogPotential(null)
    } catch (e: any) {
      setError(e.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading || !user) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (user.blog_role !== "admin") return null

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">验证管理</h1>
            <p className="text-gray-400 text-sm mt-1">触发和管理势函数验证任务</p>
          </div>
          <a
            href="/admin"
            className="text-sm text-gray-400 hover:text-white transition px-3 py-1.5 border border-gray-700 rounded-lg"
          >
            ← 返回管理后台
          </a>
        </div>

        {error && (
          <div className="mb-6 px-4 py-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
            <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-300">✕</button>
          </div>
        )}

        {/* Active job progress */}
        {activeJob && (activeJob.status === 'pending' || activeJob.status === 'running') && (
          <div className="mb-6 bg-gray-800 border border-blue-800/50 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-medium text-white">正在验证：{activePotentialName}</h3>
                <p className="text-xs text-gray-400 mt-0.5">
                  任务 ID: {activeJob.job_id}
                </p>
              </div>
              <span className={`text-xs px-2 py-1 rounded ${
                activeJob.status === 'running' ? 'bg-blue-900/50 text-blue-300' : 'bg-yellow-900/50 text-yellow-300'
              }`}>
                {activeJob.status === 'running' ? '运行中' : '排队中'}
              </span>
            </div>
            <VerificationProgressBar
              progress={activeJob.progress}
              currentStep={activeJob.current_step}
              estimatedRemainingSeconds={activeJob.estimated_remaining_seconds}
            />
          </div>
        )}

        {/* Search */}
        <div className="mb-4">
          <input
            type="text"
            placeholder="搜索势函数名称、元素..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="w-full max-w-md bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Potentials list */}
        {loadingPotentials ? (
          <div className="text-gray-500 py-8 text-center">加载势函数列表...</div>
        ) : (
          <div className="bg-gray-800/50 rounded-xl border border-gray-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-700">
                  <th className="px-4 py-2 text-center text-gray-300">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="rounded border-gray-500 bg-gray-600 text-blue-500 focus:ring-blue-500"
                    />
                  </th>
                  <th className="px-4 py-2 text-left text-gray-300">名称</th>
                  <th className="px-4 py-2 text-left text-gray-300">类型</th>
                  <th className="px-4 py-2 text-left text-gray-300">元素</th>
                  <th className="px-4 py-2 text-center text-gray-300">验证状态</th>
                  <th className="px-4 py-2 text-center text-gray-300">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(p => (
                  <tr key={p.id} className={`border-b border-gray-700/50 hover:bg-gray-700/30 transition ${selectedIds.has(p.id) ? 'bg-blue-900/20' : ''}`}>
                    <td className="px-4 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(p.id)}
                        onChange={() => toggleSelect(p.id)}
                        className="rounded border-gray-500 bg-gray-600 text-blue-500 focus:ring-blue-500"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <div className="font-medium text-white">{p.display_name || p.name}</div>
                      <div className="text-xs text-gray-500">{p.name}</div>
                    </td>
                    <td className="px-4 py-2 text-gray-300">{p.type}</td>
                    <td className="px-4 py-2 text-gray-300">{p.elements.join('-')}</td>
                    <td className="px-4 py-2 text-center">
                      <VerificationBadge grade={(p.verified_props as Record<string, unknown>)?.overall_grade as string | undefined} />
                    </td>
                    <td className="px-4 py-2 text-center">
                      <button
                        onClick={() => {
                          setDialogPotential(p)
                          setSelectedTemplate('basic')
                        }}
                        disabled={activeJob?.status === 'running' || activeJob?.status === 'pending'}
                        className="px-3 py-1 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        验证
                      </button>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-gray-500">未找到势函数</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* 批量操作浮动栏 */}
        {selectedIds.size > 0 && !hasActiveBatch && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-gray-800 border border-gray-600 rounded-xl px-6 py-3 shadow-2xl flex items-center gap-4">
            <span className="text-sm text-gray-200">已选 <span className="text-white font-bold">{selectedIds.size}</span> 个</span>
            <select
              value={batchTemplate}
              onChange={e => setBatchTemplate(e.target.value as Template)}
              className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            >
              {(Object.keys(TEMPLATE_INFO) as Template[]).map(t => (
                <option key={t} value={t}>{TEMPLATE_INFO[t].label}</option>
              ))}
            </select>
            <button
              onClick={handleBatchSubmit}
              disabled={batchSubmitting}
              className="px-4 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition disabled:opacity-50"
            >
              {batchSubmitting ? '提交中...' : '🚀 批量验证'}
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-gray-400 hover:text-white transition text-sm"
            >
              ✕
            </button>
          </div>
        )}

        {/* 批量验证进度 */}
        {hasActiveBatch && (
          <div className="mb-6 bg-gray-800 border border-blue-800/50 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-white">批量验证进度：{batchCompleted}/{batchTotal} 完成</h3>
              <span className="text-xs text-gray-400">
                {batchTotal - batchCompleted} 个任务进行中
              </span>
            </div>
            <div className="w-full bg-gray-700 rounded-full h-2 mb-3 overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${(batchCompleted / batchTotal) * 100}%` }}
              />
            </div>
            <div className="space-y-1">
              {batchEntries.map(([id, job]) => (
                <div key={id} className="flex items-center justify-between text-xs py-1">
                  <span className="text-gray-300">{job.potentialName}</span>
                  <div className="flex items-center gap-2">
                    {job.status === 'failed' && <span className="text-red-400">失败: {job.error || '未知'}</span>}
                    {job.status === 'completed' && <span className={`font-bold ${gradeColor(job.overall_grade || '')}`}>{job.overall_grade}</span>}
                    {job.status === 'running' && <span className="text-blue-400">{Math.round(job.progress * 100)}%</span>}
                    {job.status === 'pending' && <span className="text-yellow-400">排队中</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 批量结果摘要 */}
        {batchTotal > 0 && batchCompleted === batchTotal && (
          <div className="mb-6 bg-gray-800 border border-green-800/50 rounded-xl p-5">
            <h3 className="text-sm font-medium text-white mb-2">批量验证完成</h3>
            <div className="flex items-center gap-4 text-sm">
              <span className="text-gray-300">共 {batchTotal} 个，</span>
              <span className="text-green-400">A: {batchGradeA}</span>
              <span className="text-blue-400">B: {batchGradeB}</span>
              <span className="text-gray-400">其他: {batchTotal - batchGradeA - batchGradeB}</span>
              <span className="text-gray-300">
                A/B 率: {((batchGradeA + batchGradeB) / batchTotal * 100).toFixed(0)}%
              </span>
            </div>
            <button
              onClick={() => setBatchJobs({})}
              className="mt-3 text-xs text-gray-400 hover:text-white transition"
            >
              清除批量结果
            </button>
          </div>
        )}

        {/* Verification history */}
        {history.length > 0 && (
          <div className="mt-8">
            <h2 className="text-lg font-semibold text-white mb-4">验证历史</h2>
            <div className="space-y-3">
              {history.map(h => (
                <div key={h.id} className="bg-gray-800 border border-gray-700 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <span className="font-medium text-white">{h.potential_name}</span>
                      <span className="text-xs text-gray-500">{TEMPLATE_INFO[h.template as Template]?.label || h.template}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {h.status === 'completed' && h.overall_grade && (
                        <Link href={`/verify/${h.id}`} className="hover:opacity-80">
                          <VerificationBadge grade={h.overall_grade} />
                        </Link>
                      )}
                      {h.status === 'failed' && (
                        <span className="text-xs px-2 py-0.5 rounded bg-red-900/50 text-red-300">失败</span>
                      )}
                    </div>
                  </div>

                  {/* Results table for completed */}
                  {h.status === 'completed' && h.results && h.results.length > 0 && (
                    <div className="mt-3 bg-gray-700/50 rounded-lg overflow-hidden">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="bg-gray-700">
                            <th className="px-3 py-1.5 text-left text-gray-300">属性</th>
                            <th className="px-3 py-1.5 text-right text-gray-300">计算值</th>
                            <th className="px-3 py-1.5 text-right text-gray-300">参考值</th>
                            <th className="px-3 py-1.5 text-right text-gray-300">误差</th>
                            <th className="px-3 py-1.5 text-center text-gray-300">等级</th>
                          </tr>
                        </thead>
                        <tbody>
                          {h.results.map((r, i) => (
                            <tr key={i} className="border-b border-gray-700/50">
                              <td className="px-3 py-1.5 text-gray-200">{propLabel(r.property_name)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-gray-300">{r.computed_value?.toFixed(4)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-gray-400">{r.reference_value?.toFixed(4)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-gray-300">{(r.relative_error * 100).toFixed(2)}%</td>
                              <td className="px-3 py-1.5 text-center">
                                <span className={`font-bold ${gradeColor(r.grade)}`}>{r.grade}</span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <div className="mt-2 text-xs text-gray-500">
                    {new Date(h.created_at).toLocaleString('zh-CN')} · 任务 {h.id}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Template selection dialog */}
        {dialogPotential && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="bg-gray-800 border border-gray-600 rounded-xl p-6 w-full max-w-md mx-4 shadow-2xl">
              <h2 className="text-lg font-semibold text-white mb-1">选择验证模板</h2>
              <p className="text-sm text-gray-400 mb-4">
                {dialogPotential.display_name || dialogPotential.name} ({dialogPotential.elements.join('-')})
              </p>

              <div className="space-y-2 mb-6">
                {(Object.keys(TEMPLATE_INFO) as Template[]).map(t => (
                  <label
                    key={t}
                    className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition ${
                      selectedTemplate === t
                        ? 'border-blue-500 bg-blue-900/20'
                        : 'border-gray-600 hover:border-gray-500'
                    }`}
                  >
                    <input
                      type="radio"
                      name="template"
                      value={t}
                      checked={selectedTemplate === t}
                      onChange={() => setSelectedTemplate(t)}
                      className="text-blue-500"
                    />
                    <div>
                      <div className="text-sm font-medium text-white">{TEMPLATE_INFO[t].label}</div>
                      <div className="text-xs text-gray-400">{TEMPLATE_INFO[t].desc}</div>
                    </div>
                  </label>
                ))}
              </div>

              <div className="flex justify-end gap-3">
                <button
                  onClick={() => setDialogPotential(null)}
                  className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-300 hover:text-white transition"
                >
                  取消
                </button>
                <button
                  onClick={handleSubmit}
                  disabled={submitting}
                  className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {submitting ? '提交中...' : '🚀 开始验证'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
