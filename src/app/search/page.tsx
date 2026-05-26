'use client'

import { useState, Suspense } from 'react'
import Link from 'next/link'
import Pagination from '@/components/Pagination'

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
  extra: {
    irradiationRelevant?: boolean
    hasDefectData?: boolean
    hasLiquidPhase?: boolean
    validationLevel?: string
  }
  references: { doi?: string; citation?: string }[]
}

const TYPES = ['EAM', 'MEAM', 'ML', 'Buckingham', 'other']
const ELEMENTS = ['U', 'Zr', 'Mo', 'Nb', 'O', 'Fe', 'He']
const VALIDATION_LEVELS = [
  { value: 'all', label: '全部' },
  { value: 'basic', label: 'basic' },
  { value: 'benchmarked', label: 'benchmarked' },
  { value: 'production', label: 'production' },
]

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
          加载中...
        </div>
      }
    >
      <SearchContent />
    </Suspense>
  )
}

function SearchContent() {
  // Form state
  const [keyword, setKeyword] = useState('')
  const [selectedElements, setSelectedElements] = useState<string[]>([])
  const [selectedType, setSelectedType] = useState('')
  const [tempMin, setTempMin] = useState('')
  const [tempMax, setTempMax] = useState('')
  const [irradiation, setIrradiation] = useState(false)
  const [hasDefect, setHasDefect] = useState(false)
  const [hasLiquid, setHasLiquid] = useState(false)
  const [validationLevel, setValidationLevel] = useState('all')

  // Results state
  const [potentials, setPotentials] = useState<Potential[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [totalPages, setTotalPages] = useState(1)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [error, setError] = useState('')

  const toggleElement = (el: string) => {
    setSelectedElements(prev =>
      prev.includes(el) ? prev.filter(e => e !== el) : [...prev, el]
    )
  }

  const handleReset = () => {
    setKeyword('')
    setSelectedElements([])
    setSelectedType('')
    setTempMin('')
    setTempMax('')
    setIrradiation(false)
    setHasDefect(false)
    setHasLiquid(false)
    setValidationLevel('all')
    setPotentials([])
    setTotal(null)
    setTotalPages(1)
    setSearched(false)
    setError('')
    setPage(1)
  }

  const handleSearch = async () => {
    setPage(1)
    doSearch(1)
  }

  const doSearch = async (p: number) => {
    setLoading(true)
    setError('')
    setSearched(true)

    const params = new URLSearchParams()
    if (keyword.trim()) params.set('q', keyword.trim())
    if (selectedElements.length > 0) params.set('elements', selectedElements.join(','))
    if (selectedType) params.set('type', selectedType)
    if (tempMin) params.set('tempMin', tempMin)
    if (tempMax) params.set('tempMax', tempMax)
    if (irradiation) params.set('irradiation', 'true')
    if (hasDefect) params.set('hasDefect', 'true')
    if (hasLiquid) params.set('hasLiquid', 'true')
    if (validationLevel && validationLevel !== 'all') params.set('validationLevel', validationLevel)
    params.set('limit', '50')
    params.set('page', String(p))

    try {
      const res = await fetch(`/api/potentials?${params.toString()}`)
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const data = await res.json()
      setPotentials(data.potentials || [])
      setTotal(data.total ?? data.potentials?.length ?? 0)
      setTotalPages(data.totalPages || 1)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '搜索失败，请重试')
      setPotentials([])
      setTotal(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold mb-1">高级检索</h1>
        <p className="text-gray-400 text-sm mb-6">通过多维度条件精准筛选势函数</p>

        {/* Search Form */}
        <div className="bg-gray-800/60 border border-gray-700 rounded-2xl p-6 mb-8 space-y-6">

          {/* Keyword */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">关键词搜索</label>
            <input
              type="text"
              value={keyword}
              onChange={e => setKeyword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="作者、描述、体系名称..."
              className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          {/* Elements */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">元素（多选）</label>
            <div className="flex flex-wrap gap-2">
              {ELEMENTS.map(el => (
                <button
                  key={el}
                  onClick={() => toggleElement(el)}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium border transition
                    ${selectedElements.includes(el)
                      ? 'bg-blue-600 border-blue-500 text-white'
                      : 'bg-gray-700 border-gray-600 text-gray-300 hover:border-blue-500 hover:text-blue-300'
                    }`}
                >
                  {el}
                </button>
              ))}
            </div>
          </div>

          {/* Type and Validation Level */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">函数形式</label>
              <select
                value={selectedType}
                onChange={e => setSelectedType(e.target.value)}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value="">全部</option>
                {TYPES.map(t => (
                  <option key={t} value={t}>{t === 'other' ? '其他' : t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">验证等级</label>
              <select
                value={validationLevel}
                onChange={e => setValidationLevel(e.target.value)}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                {VALIDATION_LEVELS.map(v => (
                  <option key={v.value} value={v.value}>{v.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Temperature range */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">温度范围 (K)</label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                value={tempMin}
                onChange={e => setTempMin(e.target.value)}
                placeholder="最低温度"
                min={0}
                className="flex-1 bg-gray-700 border border-gray-600 rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <span className="text-gray-500 text-sm">—</span>
              <input
                type="number"
                value={tempMax}
                onChange={e => setTempMax(e.target.value)}
                placeholder="最高温度"
                min={0}
                className="flex-1 bg-gray-700 border border-gray-600 rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          {/* Nuclear material switches */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">核材料特性</label>
            <div className="flex flex-wrap gap-4">
              {[
                { label: '辐照相关', state: irradiation, setter: setIrradiation },
                { label: '缺陷数据', state: hasDefect, setter: setHasDefect },
                { label: '液相数据', state: hasLiquid, setter: setHasLiquid },
              ].map(({ label, state, setter }) => (
                <label
                  key={label}
                  className="flex items-center gap-2 cursor-pointer group"
                >
                  <input
                    type="checkbox"
                    checked={state}
                    onChange={e => setter(e.target.checked)}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500 focus:ring-offset-gray-900"
                  />
                  <span className="text-sm text-gray-300 group-hover:text-white transition">
                    {label}
                  </span>
                </label>
              ))}
            </div>
          </div>

          {/* Buttons */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleSearch}
              disabled={loading}
              className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition"
            >
              {loading ? '搜索中...' : '🔍 搜索'}
            </button>
            <button
              onClick={handleReset}
              disabled={loading}
              className="px-6 py-2.5 bg-gray-700 hover:bg-gray-600 disabled:cursor-not-allowed text-gray-300 hover:text-white text-sm font-medium rounded-lg transition"
            >
              重置
            </button>
          </div>
        </div>

        {/* Results */}
        {error && (
          <div className="bg-red-900/40 border border-red-700 rounded-xl p-4 mb-6 text-red-300 text-sm">
            {error}
          </div>
        )}

        {searched && !loading && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">搜索结果</h2>
              <span className="text-sm text-gray-400">
                共 {total !== null ? total : 0} 个结果
              </span>
            </div>

            {potentials.length === 0 ? (
              <div className="text-center py-16 text-gray-400">
                <div className="text-4xl mb-3">🔎</div>
                <p>未找到匹配的势函数</p>
                <p className="text-xs mt-1">尝试放宽筛选条件</p>
              </div>
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
                          <span className="px-2 py-0.5 bg-gray-700 rounded">
                            {p.elements.join('-')}
                          </span>
                          {p.system_name && (
                            <span className="px-2 py-0.5 bg-gray-700 rounded">{p.system_name}</span>
                          )}
                          {p.applicability?.temperatureRange && (
                            <span className="px-2 py-0.5 bg-gray-700 rounded">
                              {p.applicability.temperatureRange[0]}-
                              {p.applicability.temperatureRange[1]}K
                            </span>
                          )}
                          {p.extra?.irradiationRelevant && (
                            <span className="px-2 py-0.5 bg-orange-900/50 text-orange-300 rounded">
                              辐照相关
                            </span>
                          )}
                          {p.extra?.hasDefectData && (
                            <span className="px-2 py-0.5 bg-purple-900/50 text-purple-300 rounded">
                              缺陷数据
                            </span>
                          )}
                          {p.extra?.hasLiquidPhase && (
                            <span className="px-2 py-0.5 bg-cyan-900/50 text-cyan-300 rounded">
                              液相数据
                            </span>
                          )}
                          {p.extra?.validationLevel && p.extra.validationLevel !== 'basic' && (
                            <span className="px-2 py-0.5 bg-green-900/50 text-green-300 rounded">
                              {p.extra.validationLevel}
                            </span>
                          )}
                        </div>
                        {p.description && (
                          <p className="text-sm text-gray-400 mt-2 line-clamp-2">{p.description}</p>
                        )}
                      </div>
                      <div className="flex gap-2 ml-4 shrink-0">
                        <span className="text-xs text-blue-400 border border-blue-800 rounded px-2 py-1">
                          详情 →
                        </span>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            )}

            {!loading && potentials.length > 0 && (
              <Pagination
                currentPage={page}
                totalPages={totalPages}
                onPageChange={(p) => {
                  setPage(p)
                  doSearch(p)
                  window.scrollTo({ top: 0, behavior: 'smooth' })
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
