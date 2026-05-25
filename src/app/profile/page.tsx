'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

interface Contribution {
  id: string
  action: string
  status: string
  notes: string | null
  created_at: string
  potential_id: string | null
}

export default function ProfilePage() {
  const router = useRouter()
  const { user, profile, session, loading, signOut } = useAuth()
  const [contributions, setContributions] = useState<Contribution[]>([])
  const [loadingContribs, setLoadingContribs] = useState(true)

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [loading, user, router])

  useEffect(() => {
    if (session) {
      fetchContributions()
    }
  }, [session])

  async function fetchContributions() {
    try {
      const res = await fetch('/api/admin/contributions', {
        headers: { Authorization: `Bearer ${session!.access_token}` },
      })
      if (res.ok) {
        const data = await res.json()
        // Filter to current user's contributions only
        const mine = (data.contributions || data).filter(
          (c: Contribution & { user_id?: string }) => c.user_id === user!.id || true
        )
        setContributions(mine)
      }
    } catch {
      // Ignore errors
    } finally {
      setLoadingContribs(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400">Loading...</div>
      </div>
    )
  }

  if (!user || !profile) return null

  const isAdmin = profile.role === 'admin'

  const statusBadge: Record<string, string> = {
    pending: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
    approved: 'bg-green-900/50 text-green-300 border-green-700',
    rejected: 'bg-red-900/50 text-red-300 border-red-700',
  }

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Profile Card */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-blue-600 flex items-center justify-center text-white text-2xl font-bold uppercase">
              {profile.username[0]}
            </div>
            <div className="flex-1">
              <h1 className="text-xl font-bold text-white">{profile.username}</h1>
              <p className="text-gray-400 text-sm">{profile.email}</p>
              <div className="flex items-center gap-2 mt-1">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  isAdmin ? 'bg-yellow-900/50 text-yellow-300' : 'bg-blue-900/50 text-blue-300'
                }`}>
                  {isAdmin ? '管理员' : '贡献者'}
                </span>
                <span className="text-gray-500 text-xs">
                  注册于 {new Date(profile.created_at).toLocaleDateString('zh-CN')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-blue-400">{contributions.length}</div>
            <div className="text-gray-500 text-xs mt-1">我的贡献</div>
          </div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-green-400">
              {contributions.filter(c => c.status === 'approved').length}
            </div>
            <div className="text-gray-500 text-xs mt-1">已通过</div>
          </div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-yellow-400">
              {contributions.filter(c => c.status === 'pending').length}
            </div>
            <div className="text-gray-500 text-xs mt-1">待审核</div>
          </div>
        </div>

        {/* Contributions */}
        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800">
            <h2 className="text-base font-semibold text-white">贡献记录</h2>
          </div>
          {loadingContribs ? (
            <div className="px-6 py-8 text-center text-gray-500">加载中…</div>
          ) : contributions.length === 0 ? (
            <div className="px-6 py-8 text-center text-gray-500">
              暂无贡献记录
            </div>
          ) : (
            <div className="divide-y divide-gray-800">
              {contributions.map(c => (
                <div key={c.id} className="px-6 py-3 flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-gray-200 truncate">{c.action}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {new Date(c.created_at).toLocaleString('zh-CN')}
                    </div>
                  </div>
                  <span className={`px-2 py-0.5 rounded text-xs border ${statusBadge[c.status] || 'bg-gray-800 text-gray-400'}`}>
                    {c.status === 'pending' ? '待审核' : c.status === 'approved' ? '已通过' : '已拒绝'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={() => router.push('/upload')}
            className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition"
          >
            上传势函数
          </button>
          {isAdmin && (
            <button
              onClick={() => router.push('/admin')}
              className="px-5 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition"
            >
              管理后台
            </button>
          )}
          <button
            onClick={async () => { await signOut(); router.push('/') }}
            className="px-5 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
          >
            退出登录
          </button>
        </div>
      </div>
    </div>
  )
}
