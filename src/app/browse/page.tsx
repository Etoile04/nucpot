'use client'

import { useState, useEffect, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'

interface Potential {
  id: string
  name: string
  display_name: string
  type: string
  elements: string[]
  system_name: string
  description: string
  applicability: { temperatureRange?: number[]; phases?: string[] }
  tags: string[]
  extra: { irradiationRelevant?: boolean; hasDefectData?: boolean; validationLevel?: string }
  references: { doi?: string; citation?: string }[]
}

const TYPES = ['EAM', 'MEAM', 'ML', 'Buckingham', 'other']
const ELEMENTS = ['U', 'Zr', 'Mo', 'Nb', 'O', 'Fe', 'He']

export default function BrowsePage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">加载中...</div>}>
      <BrowseContent />
    </Suspense>
  )
}

function BrowseContent() {
  const searchParams = useSearchParams()
  const [potentials, setPotentials] = useState<Potential[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  const [selectedTypes, setSelectedTypes] = useState<string[]>(() => {
    const t = searchParams.get('type')
    return t ? [t] : []
  })
  const [selectedElements, setSelectedElements] = useState<string[]>(() => {
    const e = searchParams.get('elements')
    return e ? e.split(',') : []
  })
  const [query, setQuery] = useState(searchParams.get('q') || '')

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams()
    if (selectedTypes.length > 0) params.set('type', selectedTypes[0])
    if (selectedElements.length > 0) params.set('elements', selectedElements.join(','))
    if (query) params.set('q', query)

    fetch(`/api/potentials?${params.toString()}`)
      .then(r => r.json())
      .then(data => {
        setPotentials(data.potentials || [])
        setTotal(data.total || 0)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [selectedTypes, selectedElements, query])

  const toggleFilter = (value: string, current: string[], setter: (v: string[]) => void) => {
    setter(current.includes(value) ? current.filter(v => v !== value) : [...current, value])
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
        <Link href="/" className="text-xl font-bold tracking-tight">
          NucPot <span className="text-blue-400 text-sm font-normal">核材料势函数库</span>
        </Link>
        <div className="flex gap-6 text-sm">
          <Link href="/browse" className="text-blue-400">浏览</Link>
          <Link href="/search" className="hover:text-blue-400 transition">高级检索</Link>
          <Link href="/about" className="hover:text-blue-400 transition">关于</Link>
        </div>
      </nav>

      <div className="flex max-w-7xl mx-auto">
        {/* Sidebar Filters */}
        <aside className="w-64 shrink-0 p-6 border-r border-gray-700 min-h-[calc(100vh-60px)]">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">筛选器</h2>

          {/* Type filter */}
          <div className="mb-6">
            <h3 className="text-sm font-medium mb-2 text-gray-300">▼ 函数形式</h3>
            {TYPES.map(t => (
              <label key={t} className="flex items-center gap-2 py-1 text-sm cursor-pointer hover:text-blue-400">
                <input
                  type="checkbox"
                  checked={selectedTypes.includes(t)}
                  onChange={() => toggleFilter(t, selectedTypes, setSelectedTypes)}
                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                />
                {t === 'other' ? '其他' : t}
              </label>
            ))}
          </div>

          {/* Element filter */}
          <div className="mb-6">
            <h3 className="text-sm font-medium mb-2 text-gray-300">▼ 元素组合</h3>
            {ELEMENTS.map(e => (
              <label key={e} className="flex items-center gap-2 py-1 text-sm cursor-pointer hover:text-blue-400">
                <input
                  type="checkbox"
                  checked={selectedElements.includes(e)}
                  onChange={() => toggleFilter(e, selectedElements, setSelectedElements)}
                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                />
                {e}
              </label>
            ))}
          </div>

          <button
            onClick={() => { setSelectedTypes([]); setSelectedElements([]); setQuery('') }}
            className="text-sm text-gray-400 hover:text-white transition"
          >
            重置筛选
          </button>
        </aside>

        {/* Results */}
        <main className="flex-1 p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-xl font-semibold">势函数浏览</h1>
            <span className="text-sm text-gray-400">共 {total} 个结果</span>
          </div>

          {loading ? (
            <div className="text-gray-400 text-center py-20">加载中...</div>
          ) : potentials.length === 0 ? (
            <div className="text-gray-400 text-center py-20">未找到匹配的势函数</div>
          ) : (
            <div className="space-y-3">
              {potentials.map(p => (
                <Link
                  key={p.id}
                  href={`/potential/${p.id}`}
                  className="block bg-gray-800/50 rounded-xl p-4 border border-gray-700 hover:border-blue-500/50 transition"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="font-semibold text-blue-300">
                        {p.display_name || p.name}
                      </h3>
                      <div className="flex flex-wrap gap-2 mt-2 text-xs">
                        <span className="px-2 py-0.5 bg-blue-900/50 rounded">{p.type}</span>
                        <span className="px-2 py-0.5 bg-gray-700 rounded">{p.elements.join('-')}</span>
                        {p.system_name && (
                          <span className="px-2 py-0.5 bg-gray-700 rounded">{p.system_name}</span>
                        )}
                        {p.applicability?.temperatureRange && (
                          <span className="px-2 py-0.5 bg-gray-700 rounded">
                            {p.applicability.temperatureRange[0]}-{p.applicability.temperatureRange[1]}K
                          </span>
                        )}
                        {p.extra?.irradiationRelevant && (
                          <span className="px-2 py-0.5 bg-orange-900/50 rounded">辐照相关</span>
                        )}
                      </div>
                      {p.description && (
                        <p className="text-sm text-gray-400 mt-2 line-clamp-2">{p.description}</p>
                      )}
                    </div>
                    <div className="flex gap-2 ml-4">
                      <span className="text-xs text-blue-400 border border-blue-800 rounded px-2 py-1">
                        详情 →
                      </span>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
