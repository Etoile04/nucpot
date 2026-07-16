'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import { request } from '@/lib/api-client'

interface AdminStats {
  totalPotentials: number
  potentialsByType: Record<string, number>
  potentialsBySource: Record<string, number>
  totalUsers: number
  pendingReviews: number
}

type Tab = 'stats' | 'info'

export default function AdminPage() {
  const router = useRouter()
  const { user, loading } = useAuth()

  const [tab, setTab] = useState<Tab>('stats')
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Auth guard — redirect if not admin
  useEffect(() => {
    if (!loading) {
      if (!user) {
        router.push('/login')
      } else if (user.blog_role !== 'admin') {
        router.push('/')
      }
    }
  }, [loading, user, router])

  const fetchStats = useCallback(async () => {
    if (!user) return
    setStatsLoading(true)
    setError(null)
    try {
      // Fetch stats from backend
      const statsData = await request<{ success: boolean; data: { total_potentials: number; total_types: number; total_elements: number } }>('/api/v1/stats')
      const totalPotentials = statsData.data.total_potentials

      // Fetch pending reviews from backend
      let pendingReviews = 0
      try {
        const reviewPending = await request<{ success: boolean; data: any }>('/api/v1/review/review/pending')
        pendingReviews = Array.isArray(reviewPending.data) ? reviewPending.data.length : 0
      } catch {
        // Review endpoint may not return data, that's ok
      }

      setStats({
        totalPotentials,
        potentialsByType: {},
        potentialsBySource: {},
        totalUsers: 0,
        pendingReviews,
      })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setStatsLoading(false)
    }
  }, [user])

  useEffect(() => {
    if (user?.blog_role === 'admin' && user) {
      fetchStats()
    }
  }, [user, fetchStats])

  if (loading || !user) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (user.blog_role !== 'admin') {
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

        {/* Stats cards */}
        {statsLoading ? (
          <div className="text-gray-500 py-8 text-center">加载统计数据...</div>
        ) : stats ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <StatCard label="势函数总数" value={stats.totalPotentials} color="blue" />
            <StatCard label="待审核" value={stats.pendingReviews} color="yellow" />
            <div className="rounded-xl border border-gray-700 px-5 py-4 text-gray-400 bg-gray-800/50">
              <div className="text-sm mt-1 opacity-80">用户总数</div>
              <div className="text-3xl font-bold text-green-400">-</div>
            </div>
          </div>
        ) : (
          <div className="text-gray-500 py-8 text-center">暂无数据</div>
        )}
      </div>
    </div>
  )
}

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
