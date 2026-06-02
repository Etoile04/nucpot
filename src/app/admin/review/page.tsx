'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import { reviewApi } from '@/lib/review-api'
import type { ReviewStats, ReviewStatus } from '@/lib/nfmd-review'
import { STATUS_CONFIG } from '@/lib/nfmd-review'

export default function ReviewDashboardPage() {
  const router = useRouter()
  const { profile, loading, session } = useAuth()
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [loadingStats, setLoadingStats] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!loading && (!profile || profile.role !== 'admin')) router.push('/')
  }, [loading, profile, router])

  const api = useMemo(() => reviewApi(session), [session])

  const fetchStats = useCallback(async () => {
    setLoadingStats(true)
    setError(null)
    try {
      const { data } = await api.stats()
      setStats(data)
    } catch (e: any) {
      setError(e.message || '加载失败')
      console.error('Failed to load review stats:', e)
    } finally {
      setLoadingStats(false)
    }
  }, [api])

  useEffect(() => { if (profile?.role === 'admin') fetchStats() }, [profile, fetchStats])

  const needsAction = stats ? (stats.by_status.needs_data || 0) + (stats.by_status.needs_review || 0) + (stats.by_status.pending || 0) : 0

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold">NFMD 数据校对</h1>
            <p className="text-gray-400 mt-1">参数与文献质量审核工作台</p>
          </div>
          <button
            onClick={fetchStats}
            disabled={loadingStats}
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors"
          >
            {loadingStats ? '刷新中...' : '刷新'}
          </button>
        </div>

        {error && (
          <div className="mb-6 p-3 bg-red-900/30 border border-red-800/50 rounded-lg text-sm text-red-300">
            {error}
            <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-300">✕</button>
          </div>
        )}

        {loadingStats && !stats ? (
          <div className="animate-pulse space-y-4">
            {[1,2,3].map(i => <div key={i} className="h-32 bg-gray-800 rounded-lg" />)}
          </div>
        ) : stats ? (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
              <StatCard label="总参数" value={stats.total_params.toLocaleString()} color="text-blue-400" />
              <StatCard label="待处理" value={String(needsAction)} color="text-orange-400"
                sub={`needs_data ${stats.by_status.needs_data || 0} + needs_review ${stats.by_status.needs_review || 0}`} />
              <StatCard label="自动通过" value={String(stats.by_status.auto_approved || 0)} color="text-gray-400" />
              <StatCard label="人工通过" value={String(stats.by_status.approved || 0)} color="text-green-400" />
            </div>

            <div className="bg-gray-900 rounded-lg p-6 mb-8">
              <h2 className="text-lg font-semibold mb-4">审核状态分布</h2>
              <div className="space-y-3">
                {(Object.entries(STATUS_CONFIG) as [ReviewStatus, typeof STATUS_CONFIG[ReviewStatus]][])
                  .sort((a, b) => a[1].priority - b[1].priority)
                  .map(([status, cfg]) => {
                    const count = stats.by_status[status] || 0
                    const pct = stats.total_params > 0 ? (count / stats.total_params * 100) : 0
                    return (
                      <div key={status} className="flex items-center gap-3">
                        <span className={`text-xs px-2 py-0.5 rounded whitespace-nowrap ${cfg.bgColor} ${cfg.color}`}>
                          {cfg.label}
                        </span>
                        <div className="flex-1 bg-gray-800 rounded-full h-5 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${cfg.bgColor}`}
                            style={{ width: `${Math.max(pct, 0.5)}%` }}
                          />
                        </div>
                        <span className="text-sm text-gray-400 w-16 text-right">{count.toLocaleString()}</span>
                        <span className="text-xs text-gray-500 w-12 text-right">{pct.toFixed(1)}%</span>
                      </div>
                    )
                  })}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
              <ActionCard title="参数校对" desc={`${needsAction} 条待处理`} onClick={() => router.push('/admin/review/params')} accent="orange" />
              <ActionCard title="文献关联" desc="检查文献-参数计数匹配" onClick={() => router.push('/admin/review/literature')} accent="blue" />
              <ActionCard title="审核日志" desc="查看操作历史" onClick={() => {/* TODO */}} accent="gray" />
            </div>

            <div className="bg-gray-900 rounded-lg p-6">
              <h2 className="text-lg font-semibold mb-4">待审材料 TOP 10</h2>
              <div className="space-y-2">
                {Object.entries(stats.top_materials).map(([mat, count]) => (
                  <div key={mat} className="flex justify-between items-center py-1 border-b border-gray-800 last:border-0">
                    <span className="text-sm">{mat}</span>
                    <span className="text-sm text-gray-400">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="text-center py-20 text-gray-500">加载失败，请检查数据库连接</div>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div className="bg-gray-900 rounded-lg p-5">
      <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  )
}

function ActionCard({ title, desc, onClick, accent }: { title: string; desc: string; onClick: () => void; accent: string }) {
  const borders: Record<string, string> = {
    orange: 'border-orange-800/50 hover:border-orange-600',
    blue: 'border-blue-800/50 hover:border-blue-600',
    gray: 'border-gray-700 hover:border-gray-500',
  }
  return (
    <button onClick={onClick} className={`bg-gray-900 border ${borders[accent] || borders.gray} rounded-lg p-5 text-left transition-colors`}>
      <h3 className="font-semibold">{title}</h3>
      <p className="text-sm text-gray-400 mt-1">{desc}</p>
    </button>
  )
}
