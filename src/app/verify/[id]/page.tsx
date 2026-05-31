'use client'

import { useEffect, useState, useRef } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import VerificationBadge from '@/components/VerificationBadge'
import VerificationProgressBar from '@/components/VerificationProgressBar'

// ─── Types ────────────────────────────────────────────────────────────────────

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

interface ScoreReport {
  job_id: string
  potential_name: string
  overall_grade: string
  property_scores: PropertyResult[]
  summary: string
  created_at: string
}

// ─── 常量 ────────────────────────────────────────────────────────────────────

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

const API_BASE = process.env.NEXT_PUBLIC_AUTOCV_API_URL || 'https://verify.nucpot.dpdns.org'

// ─── Main Component ───────────────────────────────────────────────────────────

export default function VerifyReportPage() {
  const params = useParams()
  const id = params.id as string

  const [job, setJob] = useState<VerifyJob | null>(null)
  const [report, setReport] = useState<ScoreReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showJson, setShowJson] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 加载任务状态
  const loadJob = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/verification/${id}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data: VerifyJob = await r.json()
      setJob(data)

      if (data.status === 'completed') {
        // 加载报告
        try {
          const rr = await fetch(`${API_BASE}/api/verification/${id}/report`)
          if (rr.ok) {
            const reportData: ScoreReport = await rr.json()
            setReport(reportData)
          }
        } catch {
          // 报告加载失败不影响展示
        }
        // 停止轮询
        if (pollingRef.current) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
        }
        setLoading(false)
      } else if (data.status === 'failed') {
        setLoading(false)
        if (pollingRef.current) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
        }
      }
      // running/pending 继续轮询
    } catch (e: any) {
      setError(e.message || '加载失败')
      setLoading(false)
    }
  }

  useEffect(() => {
    loadJob()
    pollingRef.current = setInterval(loadJob, 3000)
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (loading && !job) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-red-400">{error}</div>
      </div>
    )
  }

  if (!job) return null

  const isCompleted = job.status === 'completed'
  const isFailed = job.status === 'failed'
  const isRunning = job.status === 'running' || job.status === 'pending'

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-4xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/admin/verify"
              className="text-sm text-gray-400 hover:text-white transition px-3 py-1.5 border border-gray-700 rounded-lg"
            >
              ← 返回
            </Link>
            <h1 className="text-2xl font-bold text-white">验证报告</h1>
          </div>
          <span className="text-xs text-gray-500 font-mono">ID: {id}</span>
        </div>

        {/* 运行中/排队中 — 进度条 */}
        {isRunning && (
          <div className="mb-6 bg-gray-800 border border-blue-800/50 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <span className={`text-xs px-2 py-1 rounded ${
                job.status === 'running' ? 'bg-blue-900/50 text-blue-300' : 'bg-yellow-900/50 text-yellow-300'
              }`}>
                {job.status === 'running' ? '运行中' : '排队中'}
              </span>
            </div>
            <VerificationProgressBar
              progress={job.progress}
              currentStep={job.current_step}
              estimatedRemainingSeconds={job.estimated_remaining_seconds}
            />
          </div>
        )}

        {/* 失败 */}
        {isFailed && (
          <div className="mb-6 bg-red-900/30 border border-red-700 rounded-xl p-5 text-center">
            <p className="text-red-300">验证任务失败</p>
          </div>
        )}

        {/* 完成 — 报告内容 */}
        {isCompleted && (
          <>
            {/* 势函数名称 + 整体评级 */}
            <div className="mb-6 bg-gray-800 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-white">
                    {report?.potential_name || job.job_id}
                  </h2>
                  <div className="flex items-center gap-3 mt-1 text-sm text-gray-400">
                    {report?.created_at && (
                      <span>时间: {new Date(report.created_at).toLocaleString('zh-CN')}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => window.open(`${API_BASE}/api/verification/${id}/export?format=json`, '_blank')}
                    className="px-3 py-1.5 text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition"
                  >
                    📄 JSON
                  </button>
                  <button
                    onClick={() => window.open(`${API_BASE}/api/verification/${id}/export?format=pdf`, '_blank')}
                    className="px-3 py-1.5 text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition"
                  >
                    📑 PDF
                  </button>
                  <VerificationBadge grade={report?.overall_grade || job.overall_grade} size="lg" />
                </div>
              </div>
            </div>

            {/* 摘要 */}
            {report?.summary && (
              <div className="mb-6 bg-gray-800 border border-gray-700 rounded-xl p-5">
                <h3 className="text-sm font-medium text-gray-300 mb-2">摘要</h3>
                <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
                  {report.summary}
                </p>
              </div>
            )}

            {/* 属性评分表格 */}
            {report?.property_scores && report.property_scores.length > 0 && (
              <div className="mb-6 bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
                <div className="px-5 py-3 border-b border-gray-700">
                  <h3 className="text-sm font-medium text-gray-300">属性评分</h3>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-700/50">
                      <th className="px-5 py-2 text-left text-gray-300">属性</th>
                      <th className="px-5 py-2 text-right text-gray-300">计算值</th>
                      <th className="px-5 py-2 text-right text-gray-300">参考值</th>
                      <th className="px-5 py-2 text-right text-gray-300">误差</th>
                      <th className="px-5 py-2 text-center text-gray-300">等级</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.property_scores.map((s, i) => (
                      <tr key={i} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition">
                        <td className="px-5 py-2 text-gray-200">{propLabel(s.property_name)}</td>
                        <td className="px-5 py-2 text-right font-mono text-gray-300">
                          {s.computed_value?.toFixed(4)}
                          {s.unit ? <span className="text-gray-500 ml-1 text-xs">{s.unit}</span> : null}
                        </td>
                        <td className="px-5 py-2 text-right font-mono text-gray-400">
                          {s.reference_value?.toFixed(4)}
                        </td>
                        <td className="px-5 py-2 text-right font-mono text-gray-300">
                          {(s.relative_error * 100).toFixed(2)}%
                        </td>
                        <td className="px-5 py-2 text-center">
                          <span className={`font-bold ${gradeColor(s.grade)}`}>{s.grade}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* 无报告数据时显示 job 的 results */}
            {!report && job.results && job.results.length > 0 && (
              <div className="mb-6 bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
                <div className="px-5 py-3 border-b border-gray-700">
                  <h3 className="text-sm font-medium text-gray-300">验证结果</h3>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-700/50">
                      <th className="px-5 py-2 text-left text-gray-300">属性</th>
                      <th className="px-5 py-2 text-right text-gray-300">计算值</th>
                      <th className="px-5 py-2 text-right text-gray-300">参考值</th>
                      <th className="px-5 py-2 text-right text-gray-300">误差</th>
                      <th className="px-5 py-2 text-center text-gray-300">等级</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.results.map((s, i) => (
                      <tr key={i} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition">
                        <td className="px-5 py-2 text-gray-200">{propLabel(s.property_name)}</td>
                        <td className="px-5 py-2 text-right font-mono text-gray-300">
                          {s.computed_value?.toFixed(4)}
                          {s.unit ? <span className="text-gray-500 ml-1 text-xs">{s.unit}</span> : null}
                        </td>
                        <td className="px-5 py-2 text-right font-mono text-gray-400">
                          {s.reference_value?.toFixed(4)}
                        </td>
                        <td className="px-5 py-2 text-right font-mono text-gray-300">
                          {(s.relative_error * 100).toFixed(2)}%
                        </td>
                        <td className="px-5 py-2 text-center">
                          <span className={`font-bold ${gradeColor(s.grade)}`}>{s.grade}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {/* 详细 JSON (可折叠) */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
          <button
            onClick={() => setShowJson(prev => !prev)}
            className="w-full px-5 py-3 flex items-center justify-between text-sm text-gray-300 hover:text-white transition"
          >
            <span className="font-medium">详细 JSON</span>
            <span className="text-gray-500">{showJson ? '▲' : '▼'}</span>
          </button>
          {showJson && (
            <div className="px-5 pb-4">
              <pre className="bg-gray-900 rounded-lg p-4 text-xs font-mono text-gray-300 overflow-x-auto max-h-96 overflow-y-auto">
                {JSON.stringify(report || job, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
