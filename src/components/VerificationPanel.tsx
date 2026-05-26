'use client'

import { useState, useEffect, useCallback } from 'react'
import type { VerificationTemplate, VerificationResult, VerificationSubmitRequest } from '@/lib/types'
import VerificationBadge from './VerificationBadge'

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
  const colors: Record<string, string> = {
    A: 'text-green-400',
    B: 'text-blue-400',
    C: 'text-yellow-400',
    D: 'text-orange-400',
    F: 'text-red-400',
  }
  return colors[grade.toUpperCase()] || 'text-gray-400'
}

interface VerificationPanelProps {
  potentialName: string
}

export default function VerificationPanel({ potentialName }: VerificationPanelProps) {
  const [templates, setTemplates] = useState<VerificationTemplate[]>([])
  const [selectedTemplate, setSelectedTemplate] = useState<string>('')
  const [loadingTemplates, setLoadingTemplates] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Verification job state
  const [jobId, setJobId] = useState<number | null>(null)
  const [result, setResult] = useState<VerificationResult | null>(null)
  const [polling, setPolling] = useState(false)

  // Load templates
  useEffect(() => {
    fetch('/api/verify/templates')
      .then(r => {
        if (!r.ok) throw new Error('Service unavailable')
        return r.json()
      })
      .then((data: VerificationTemplate[]) => {
        setTemplates(data)
        if (data.length > 0) setSelectedTemplate(data[0].id)
      })
      .catch(() => setTemplates([]))
      .finally(() => setLoadingTemplates(false))
  }, [])

  // Poll for results
  const pollResult = useCallback(async (id: number) => {
    setPolling(true)
    const maxAttempts = 60
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const r = await fetch(`/api/verify/${id}`)
        if (!r.ok) continue
        const data: VerificationResult = await r.json()
        setResult(data)
        if (data.status === 'completed' || data.status === 'failed') {
          setPolling(false)
          return
        }
      } catch {
        // ignore fetch errors during polling
      }
      await new Promise(res => setTimeout(res, 3000))
    }
    setPolling(false)
    setError('验证超时，请稍后刷新查看结果')
  }, [])

  // Submit verification
  const handleSubmit = async () => {
    if (!selectedTemplate) return
    setSubmitting(true)
    setError(null)
    setResult(null)

    const body: VerificationSubmitRequest = {
      potential_name: potentialName,
      template: selectedTemplate,
    }

    try {
      const r = await fetch('/api/verify/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: '提交失败' }))
        throw new Error(err.error || `HTTP ${r.status}`)
      }

      const data = await r.json()
      setJobId(data.id)
      pollResult(data.id)
    } catch (e: any) {
      setError(e.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // Fetch report for completed job
  const [reportData, setReportData] = useState<any>(null)
  useEffect(() => {
    if (result?.status !== 'completed' || !jobId) return
    fetch(`/api/verify/${jobId}/report`)
      .then(r => r.ok ? r.json() : null)
      .then(setReportData)
      .catch(() => {})
  }, [result?.status, jobId])

  const template = templates.find(t => t.id === selectedTemplate)

  return (
    <div className="space-y-6">
      <h3 className="text-sm font-semibold text-gray-400 uppercase mb-4">势函数验证</h3>

      {/* Service unavailable */}
      {loadingTemplates && (
        <div className="text-gray-500 text-sm">加载验证模板...</div>
      )}

      {!loadingTemplates && templates.length === 0 && (
        <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700 text-sm text-gray-400">
          验证服务暂不可用。请确认后端验证服务 (nucpot-autovc) 是否已启动。
        </div>
      )}

      {templates.length > 0 && (
        <>
          {/* Template selector + submit */}
          <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700 space-y-4">
            <div>
              <label className="block text-sm text-gray-300 mb-2">选择验证模板</label>
              <select
                value={selectedTemplate}
                onChange={e => setSelectedTemplate(e.target.value)}
                className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
              >
                {templates.map(t => (
                  <option key={t.id} value={t.id}>{t.name} — {t.description} (~{t.estimated_time})</option>
                ))}
              </select>
            </div>

            {template && (
              <div className="text-xs text-gray-500">
                验证属性: {template.properties.map(p => propLabel(p)).join('、')}
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={submitting || polling}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                submitting || polling
                  ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-500 text-white'
              }`}
            >
              {submitting ? '提交中...' : polling ? '验证运行中...' : '🚀 开始验证'}
            </button>
          </div>

          {/* Status indicators */}
          {polling && (
            <div className="flex items-center gap-2 text-sm text-yellow-400">
              <span className="animate-spin inline-block w-4 h-4 border-2 border-yellow-400 border-t-transparent rounded-full" />
              验证正在运行，请等待...
            </div>
          )}

          {error && (
            <div className="bg-red-900/20 border border-red-800 rounded-lg p-3 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Results */}
          {result && result.status === 'failed' && (
            <div className="bg-red-900/20 border border-red-800 rounded-lg p-3 text-sm text-red-400">
              验证失败: {result.summary || '未知错误'}
            </div>
          )}

          {result && result.status === 'completed' && (
            <div className="space-y-4">
              {/* Overall grade */}
              <div className="flex items-center gap-3">
                <span className="text-sm text-gray-300">综合评级:</span>
                <VerificationBadge grade={result.overall_grade} />
                {result.summary && <span className="text-sm text-gray-400">— {result.summary}</span>}
              </div>

              {/* Results table */}
              {(reportData?.results || result.results) && (
                <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-gray-700">
                        <th className="px-4 py-2 text-left text-gray-300">属性</th>
                        <th className="px-4 py-2 text-right text-gray-300">计算值</th>
                        <th className="px-4 py-2 text-right text-gray-300">参考值</th>
                        <th className="px-4 py-2 text-left text-gray-300">单位</th>
                        <th className="px-4 py-2 text-right text-gray-300">相对误差</th>
                        <th className="px-4 py-2 text-center text-gray-300">等级</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(reportData?.results || result.results || []).map((r: any, i: number) => (
                        <tr key={i} className="border-b border-gray-700/50 hover:bg-gray-700/50 transition">
                          <td className="px-4 py-2 text-gray-200">{propLabel(r.property_name)}</td>
                          <td className="px-4 py-2 text-right font-mono text-gray-300">{r.computed_value?.toFixed(4)}</td>
                          <td className="px-4 py-2 text-right font-mono text-gray-400">{r.reference_value?.toFixed(4)}</td>
                          <td className="px-4 py-2 text-gray-400">{r.unit}</td>
                          <td className="px-4 py-2 text-right font-mono text-gray-300">
                            {(r.relative_error * 100).toFixed(2)}%
                          </td>
                          <td className="px-4 py-2 text-center">
                            <span className={`font-bold ${gradeColor(r.grade)}`}>{r.grade}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
