'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import { reviewApi } from '@/lib/review-api'

const PAGE_SIZE = 30

interface LitRecord {
  id: string
  title: string | null
  parameter_count: number | null
  review_status: string | null
  review_notes: string | null
  reviewed_at: string | null
  actual_count?: number
}

export default function ReviewLiteraturePage() {
  const router = useRouter()
  const { profile, loading: authLoading, session } = useAuth()

  const [lits, setLits] = useState<LitRecord[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')

  const api = useMemo(() => reviewApi(session), [session])

  const fetchLits = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const { data } = await api.queueLiterature({
        status: null,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      })
      const result = data.data || { data: [], total: 0 }
      const records: LitRecord[] = result.data || []
      setTotalCount(result.total || 0)

      // Batch fetch param counts
      const enriched = await Promise.all(records.map(async (lit: LitRecord) => {
        try {
          const { count } = await api.countParamsForLit(lit.id)
          return { ...lit, actual_count: count || 0 }
        } catch {
          return { ...lit, actual_count: 0 }
        }
      }))
      setLits(enriched)
    } catch (e: any) {
      setError(e.message || '加载失败')
      console.error(e)
    } finally {
      setIsLoading(false)
    }
  }, [page, search, api])

  useEffect(() => { if (profile?.role === 'admin') fetchLits() }, [profile, fetchLits])

  const handleFixCount = async (lit: LitRecord) => {
    const actual = lit.actual_count || 0
    if (actual === lit.parameter_count) return
    try {
      await api.fixLitCount(lit.id, actual, lit.parameter_count || 0)
      fetchLits()
    } catch (e: any) {
      alert(`修正失败: ${e.message}`)
    }
  }

  const mismatches = lits.filter(l => l.parameter_count !== l.actual_count)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.push('/admin/review')} className="text-gray-400 hover:text-white transition-colors">← 返回</button>
          <h1 className="text-xl font-bold">文献关联校对</h1>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-900/30 border border-red-800/50 rounded-lg text-sm text-red-300">
            {error}
            <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-300">✕</button>
          </div>
        )}

        <div className="mb-4 text-sm text-gray-400">
          {mismatches.length} 条计数不匹配（共 {totalCount} 条文献）
        </div>

        <div className="bg-gray-900 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase">
                <th className="px-4 py-3 text-left">文献</th>
                <th className="px-4 py-3 text-right">记录计数</th>
                <th className="px-4 py-3 text-right">实际计数</th>
                <th className="px-4 py-3 text-left">差异</th>
                <th className="px-4 py-3 text-left">状态</th>
                <th className="px-4 py-3 w-24">操作</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td colSpan={6} className="px-4 py-3"><div className="h-4 bg-gray-800 rounded animate-pulse" /></td>
                  </tr>
                ))
              ) : lits.map(lit => {
                const diff = (lit.actual_count || 0) - (lit.parameter_count || 0)
                const isMismatch = diff !== 0
                return (
                  <tr key={lit.id} className={`border-b border-gray-800/50 ${isMismatch ? 'bg-orange-900/10' : ''}`}>
                    <td className="px-4 py-2 max-w-[300px]">
                      <p className="truncate">{lit.title || lit.id}</p>
                      <p className="text-xs text-gray-500 truncate">{lit.id}</p>
                    </td>
                    <td className="px-4 py-2 text-right font-mono">{lit.parameter_count ?? '—'}</td>
                    <td className="px-4 py-2 text-right font-mono">{lit.actual_count ?? '—'}</td>
                    <td className="px-4 py-2">
                      {diff !== 0 ? (
                        <span className={`text-xs ${diff > 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {diff > 0 ? '+' : ''}{diff}
                        </span>
                      ) : <span className="text-xs text-gray-600">✓</span>}
                    </td>
                    <td className="px-4 py-2">
                      {lit.review_status ? (
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          lit.review_status === 'approved' ? 'bg-green-900/50 text-green-400' : 'bg-gray-800 text-gray-400'
                        }`}>{lit.review_status}</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2">
                      {isMismatch && (
                        <button
                          onClick={() => handleFixCount(lit)}
                          className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 rounded transition-colors"
                        >
                          修正
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
            <span className="text-sm text-gray-500">第 {page}/{Math.ceil(totalCount / PAGE_SIZE) || 1} 页</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
                className="px-3 py-1 bg-gray-800 rounded text-sm disabled:opacity-30">上一页</button>
              <button onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(totalCount / PAGE_SIZE)}
                className="px-3 py-1 bg-gray-800 rounded text-sm disabled:opacity-30">下一页</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
