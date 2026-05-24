'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'

interface Potential {
  id: string
  name: string
  display_name: string
  type: string
  elements: string[]
  system_name: string
  updated_at: string
}

interface Stats {
  totalPotentials: number
  totalTypes: number
  totalElements: number
  types: string[]
  elements: string[]
  recent: Potential[]
}

const QUICK_FILTERS = [
  { label: 'U-Zr', elements: 'U,Zr' },
  { label: 'U-Mo', elements: 'U,Mo' },
  { label: 'UO₂', elements: 'U,O' },
  { label: 'Zr', elements: 'Zr' },
  { label: 'Zr-Nb', elements: 'Zr,Nb' },
  { label: 'Fe', elements: 'Fe' },
]

export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    fetch('/api/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(console.error)
  }, [])

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      {/* Hero */}
      <section className="max-w-4xl mx-auto px-6 pt-20 pb-16 text-center">
        <h1 className="text-4xl font-bold mb-4">
          核材料原子间势函数开放平台
        </h1>
        <p className="text-gray-400 text-lg mb-8">
          面向核燃料、包壳和结构材料的势函数存储、检索与共享
        </p>

        {/* Search */}
        <form action="/browse" method="get" className="flex gap-2 max-w-2xl mx-auto mb-6">
          <input
            type="text"
            name="q"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="搜索势函数... (如: U-Zr EAM)"
            className="flex-1 px-4 py-3 rounded-lg bg-gray-700 border border-gray-600 text-white placeholder-gray-400 focus:outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 rounded-lg font-medium transition"
          >
            搜索
          </button>
        </form>

        {/* Quick filters */}
        <div className="flex flex-wrap justify-center gap-2">
          {QUICK_FILTERS.map(f => (
            <Link
              key={f.label}
              href={`/browse?elements=${f.elements}`}
              className="px-3 py-1.5 bg-gray-700/50 hover:bg-gray-600 rounded-full text-sm border border-gray-600 transition"
            >
              {f.label}
            </Link>
          ))}
          <Link
            href="/browse"
            className="px-3 py-1.5 text-gray-400 hover:text-white text-sm transition"
          >
            全部 →
          </Link>
        </div>
      </section>

      {/* Stats */}
      {stats && (
        <section className="max-w-4xl mx-auto px-6 mb-16">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { value: stats.totalPotentials, label: '势函数' },
              { value: stats.totalElements, label: '元素' },
              { value: stats.totalTypes, label: '函数形式' },
              { value: stats.types.length > 0 ? 'LAMMPS' : '-', label: '软件支持' },
            ].map((s, i) => (
              <div key={i} className="bg-gray-800/50 rounded-xl p-4 text-center border border-gray-700">
                <div className="text-3xl font-bold text-blue-400">{s.value}</div>
                <div className="text-sm text-gray-400 mt-1">{s.label}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent */}
      {stats && stats.recent.length > 0 && (
        <section className="max-w-4xl mx-auto px-6 pb-16">
          <h2 className="text-lg font-semibold mb-4">最近更新</h2>
          <div className="space-y-2">
            {stats.recent.map(p => (
              <Link
                key={p.id}
                href={`/potential/${p.id}`}
                className="flex items-center justify-between bg-gray-800/50 rounded-lg px-4 py-3 border border-gray-700 hover:border-blue-500/50 transition"
              >
                <div>
                  <div className="font-medium">{p.display_name || p.name}</div>
                  <div className="text-sm text-gray-400">
                    {p.elements.join('-')} · {p.type} · {p.system_name}
                  </div>
                </div>
                <div className="text-xs text-gray-500">
                  {new Date(p.updated_at).toLocaleDateString('zh-CN')}
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Footer */}
      <footer className="border-t border-gray-700 px-6 py-6 text-center text-sm text-gray-500">
        NucPot 核材料势函数库 · 面向核材料研究的开放平台
      </footer>
    </div>
  )
}
