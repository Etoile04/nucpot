'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useAuth } from '@/components/AuthProvider'

interface AdminStats {
  totalPotentials: number
  potentialsByType: Record<string, number>
  potentialsBySource: Record<string, number>
  totalContributions: number
  pendingContributions: number
  totalUsers: number
  usersByRole: Record<string, number>
}

interface ContributionItem {
  id: string
  potential_id: string | null
  user_id: string | null
  action: string
  status: string
  notes: string | null
  created_at: string
  profiles: {
    id: string
    username: string
    full_name: string | null
    email: string | null
    role: string
  } | null
  potentials: {
    id: string
    name: string
    display_name: string | null
    type: string
    elements: string[]
    status: string
  } | null
}

type Tab = 'stats' | 'contributions'

export default function AdminPage() {
  const router = useRouter()
  const { profile, session, loading } = useAuth()

  const [tab, setTab] = useState<Tab>('stats')
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [contributions, setContributions] = useState<ContributionItem[]>([])
  const [contribTotal, setContribTotal] = useState(0)
  const [statsLoading, setStatsLoading] = useState(false)
  const [contribLoading, setContribLoading] = useState(false)
  const [actionInProgress, setActionInProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Auth guard — redirect if not admin
  useEffect(() => {
    if (!loading) {
      if (!profile) {
        router.push('/login')
      } else if (profile.role !== 'admin') {
        router.push('/')
      }
    }
  }, [loading, profile, router])

  const fetchStats = useCallback(async () => {
    if (!session?.access_token) return
    setStatsLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/stats', {
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed to load stats')
      setStats(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setStatsLoading(false)
    }
  }, [session?.access_token])

  const fetchContributions = useCallback(async (statusFilter?: string) => {
    if (!session?.access_token) return
    setContribLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ limit: '50' })
      if (statusFilter) params.set('status', statusFilter)
      const res = await fetch(`/api/admin/contributions?${params}`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed to load contributions')
      setContributions(data.contributions || [])
      setContribTotal(data.total || 0)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setContribLoading(false)
    }
  }, [session?.access_token])

  useEffect(() => {
    if (profile?.role === 'admin' && session?.access_token) {
      fetchStats()
      fetchContributions('pending')
    }
  }, [profile, session?.access_token, fetchStats, fetchContributions])

  async function handleAction(contributionId: string, action: 'approve' | 'reject') {
    if (!session?.access_token) return
    setActionInProgress(contributionId)
    setError(null)
    try {
      const res = await fetch('/api/admin/contributions', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ contributionId, action }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Action failed')
      // Remove from list (we're viewing pending only)
      setContributions(prev => prev.filter(c => c.id !== contributionId))
      setContribTotal(prev => Math.max(0, prev - 1))
      // Refresh stats
      fetchStats()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setActionInProgress(null)
    }
  }

  if (loading || !profile) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (profile.role !== 'admin') {
    return null
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white">管理后台</h1>
          <p className="text-gray-400 text-sm mt-1">NucPot 数据库管理控制台</p>
        </div>

        {error && (
          <div className="mb-6 px-4 py-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-gray-800 p-1 rounded-xl w-fit">
          <button
            onClick={() => setTab('stats')}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition ${
              tab === 'stats'
                ? 'bg-gray-700 text-white shadow'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            统计概览
          </button>
          <Link href="/admin/review" className="px-5 py-2 rounded-lg text-sm font-medium transition text-gray-400 hover:text-gray-200">
            NFMD 校对
          </Link>
          <button
            onClick={() => setTab('contributions')}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition relative ${
              tab === 'contributions'
                ? 'bg-gray-700 text-white shadow'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            贡献审核
            {(stats?.pendingContributions ?? 0) > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-yellow-500 rounded-full text-xs text-black font-bold flex items-center justify-center">
                {stats!.pendingContributions}
              </span>
            )}
          </button>
        </div>

        {/* === STATS TAB === */}
        {tab === 'stats' && (
          <div>
            {statsLoading ? (
              <div className="text-gray-500 py-8 text-center">加载统计数据...</div>
            ) : stats ? (
              <div className="space-y-6">
                {/* Summary cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <StatCard label="势函数总数" value={stats.totalPotentials} color="blue" />
                  <StatCard label="用户总数" value={stats.totalUsers} color="green" />
                  <StatCard label="贡献总数" value={stats.totalContributions} color="purple" />
                  <StatCard label="待审核" value={stats.pendingContributions} color="yellow" />
                </div>

                {/* By Type */}
                <SectionCard title="按类型分布">
                  <BarChart data={stats.potentialsByType} />
                </SectionCard>

                {/* By Source */}
                <SectionCard title="按来源分布">
                  <BarChart data={stats.potentialsBySource} />
                </SectionCard>

                {/* Users by role */}
                <SectionCard title="用户角色分布">
                  <BarChart data={stats.usersByRole} />
                </SectionCard>
              </div>
            ) : (
              <div className="text-gray-500 py-8 text-center">暂无数据</div>
            )}
          </div>
        )}

        {/* === CONTRIBUTIONS TAB === */}
        {tab === 'contributions' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <p className="text-gray-400 text-sm">
                待审核贡献 <span className="text-yellow-400 font-medium">{contribTotal}</span> 条
              </p>
              <button
                onClick={() => fetchContributions('pending')}
                className="text-xs text-gray-400 hover:text-white transition px-3 py-1.5 border border-gray-700 rounded-lg"
              >
                刷新
              </button>
            </div>

            {contribLoading ? (
              <div className="text-gray-500 py-8 text-center">加载中...</div>
            ) : contributions.length === 0 ? (
              <div className="py-16 text-center text-gray-500">
                <div className="text-4xl mb-3">✅</div>
                <div>没有待审核的贡献</div>
              </div>
            ) : (
              <div className="space-y-3">
                {contributions.map(c => (
                  <ContributionRow
                    key={c.id}
                    contribution={c}
                    actionInProgress={actionInProgress}
                    onAction={handleAction}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function StatCard({
  label, value, color,
}: {
  label: string
  value: number
  color: 'blue' | 'green' | 'purple' | 'yellow'
}) {
  const colorMap = {
    blue: 'text-blue-400 bg-blue-900/20 border-blue-800/40',
    green: 'text-green-400 bg-green-900/20 border-green-800/40',
    purple: 'text-purple-400 bg-purple-900/20 border-purple-800/40',
    yellow: 'text-yellow-400 bg-yellow-900/20 border-yellow-800/40',
  }
  return (
    <div className={`rounded-xl border px-5 py-4 ${colorMap[color]}`}>
      <div className="text-3xl font-bold">{value.toLocaleString()}</div>
      <div className="text-sm mt-1 opacity-80">{label}</div>
    </div>
  )
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
      <h3 className="text-sm font-medium text-gray-300 mb-4">{title}</h3>
      {children}
    </div>
  )
}

function BarChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
  const max = Math.max(...entries.map(([, v]) => v), 1)

  if (entries.length === 0) {
    return <div className="text-gray-500 text-sm py-2">暂无数据</div>
  }

  return (
    <div className="space-y-2">
      {entries.map(([key, count]) => (
        <div key={key} className="flex items-center gap-3">
          <div className="w-24 text-right text-xs text-gray-400 truncate shrink-0">{key}</div>
          <div className="flex-1 bg-gray-700 rounded-full h-4 overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all"
              style={{ width: `${(count / max) * 100}%` }}
            />
          </div>
          <div className="w-8 text-xs text-gray-300 text-right shrink-0">{count}</div>
        </div>
      ))}
    </div>
  )
}

function ContributionRow({
  contribution: c,
  actionInProgress,
  onAction,
}: {
  contribution: ContributionItem
  actionInProgress: string | null
  onAction: (id: string, action: 'approve' | 'reject') => void
}) {
  const isLoading = actionInProgress === c.id

  const actionLabels: Record<string, string> = {
    create: '新建',
    update: '更新',
    review: '审阅',
  }

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {/* Potential info */}
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-xs px-2 py-0.5 bg-gray-700 rounded text-gray-300">
              {actionLabels[c.action] || c.action}
            </span>
            {c.potentials ? (
              <span className="text-sm font-medium text-white truncate">
                {c.potentials.display_name || c.potentials.name}
              </span>
            ) : (
              <span className="text-sm text-gray-500">已删除势函数</span>
            )}
            {c.potentials && (
              <span className="text-xs text-gray-500">
                [{c.potentials.type}] {c.potentials.elements?.join('-')}
              </span>
            )}
          </div>

          {/* User info */}
          <div className="text-xs text-gray-400">
            贡献者：
            <span className="text-gray-300">
              {c.profiles?.username || c.profiles?.full_name || '未知用户'}
            </span>
            {c.profiles?.email && (
              <span className="ml-1 text-gray-500">({c.profiles.email})</span>
            )}
            <span className="mx-2">·</span>
            {new Date(c.created_at).toLocaleString('zh-CN', {
              year: 'numeric', month: '2-digit', day: '2-digit',
              hour: '2-digit', minute: '2-digit',
            })}
          </div>

          {c.notes && (
            <div className="mt-2 text-xs text-gray-400 bg-gray-700/50 rounded px-3 py-2">
              备注：{c.notes}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 shrink-0">
          <button
            onClick={() => onAction(c.id, 'approve')}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs rounded-lg bg-green-700 hover:bg-green-600 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? '处理中...' : '通过'}
          </button>
          <button
            onClick={() => onAction(c.id, 'reject')}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs rounded-lg bg-red-800 hover:bg-red-700 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? '处理中...' : '拒绝'}
          </button>
        </div>
      </div>
    </div>
  )
}
