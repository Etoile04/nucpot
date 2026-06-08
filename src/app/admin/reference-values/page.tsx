'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import type { RefValueMatrixData } from '@/lib/types'
import EditValueModal from '@/components/EditValueModal'

const PROPERTIES = [
  'lattice_constant',
  'cohesive_energy',
  'C11',
  'C12',
  'C44',
  'C33',
  'bulk_modulus',
  'vacancy_formation_energy',
] as const

const PROPERTY_LABELS: Record<string, string> = {
  lattice_constant: '晶格常数',
  cohesive_energy: '内聚能',
  C11: 'C₁₁',
  C12: 'C₁₂',
  C44: 'C₄₄',
  C33: 'C₃₃',
  bulk_modulus: '体模量',
  vacancy_formation_energy: '空位形成能',
}

type ExpandedCell = {
  systemIdx: number
  property: string
}

// Detail shown when a cell is expanded
interface CellDetail {
  id: string
  value: number
  unit: string
  source: string | null
  source_doi: string | null
  method: string | null
  confidence: string
  needs_review: boolean
  status: string
}

export default function ReferenceValuesPage() {
  const router = useRouter()
  const { profile, session, loading } = useAuth()

  const [matrix, setMatrix] = useState<RefValueMatrixData | null>(null)
  const [loadingData, setLoadingData] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<ExpandedCell | null>(null)
  const [cellDetail, setCellDetail] = useState<CellDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [modalRefValue, setModalRefValue] = useState<any>(null)

  // Auth guard
  useEffect(() => {
    if (!loading) {
      if (!profile) {
        router.push('/login')
      } else if (profile.role !== 'admin') {
        router.push('/')
      }
    }
  }, [loading, profile, router])

  const fetchMatrix = useCallback(async () => {
    if (!session?.access_token) return
    setLoadingData(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/reference-values/matrix', {
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed to load matrix')
      setMatrix(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoadingData(false)
    }
  }, [session?.access_token])

  useEffect(() => {
    if (profile?.role === 'admin' && session?.access_token) {
      fetchMatrix()
    }
  }, [profile, session?.access_token, fetchMatrix])

  async function handleCellClick(systemIdx: number, property: string) {
    const sys = matrix?.systems[systemIdx]
    if (!sys) return

    const key = `${systemIdx}-${property}`
    if (expanded && expanded.systemIdx === systemIdx && expanded.property === property) {
      setExpanded(null)
      setCellDetail(null)
      return
    }

    setExpanded({ systemIdx, property })
    setDetailLoading(true)
    setCellDetail(null)
    try {
      const res = await fetch(
        `/api/admin/reference-values/detail?element_system=${sys.element_system}&phase=${sys.phase ?? ''}&property=${property}`,
        { headers: { Authorization: `Bearer ${session!.access_token}` } }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed')
      setCellDetail(data)
    } catch {
      setCellDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  function getCellStyle(prop: RefValueMatrixData['systems'][0]['properties'][string] | null | undefined) {
    if (!prop) return 'bg-gray-800 text-gray-600'
    if (prop.status === 'rejected') return 'bg-gray-700 text-gray-500 line-through'
    if (prop.confidence === 'high' && !prop.needs_review) return 'bg-green-900/40 text-green-300'
    if (prop.confidence === 'medium' && prop.needs_review) return 'bg-yellow-900/40 text-yellow-300'
    if (prop.confidence === 'low' && prop.needs_review) return 'bg-red-900/40 text-red-300'
    // Default: medium without review
    return 'bg-yellow-900/20 text-yellow-200'
  }

  function getStatusIcon(prop: RefValueMatrixData['systems'][0]['properties'][string] | null | undefined) {
    if (!prop) return ''
    if (prop.status === 'rejected') return ' ✕'
    if (prop.confidence === 'high' && !prop.needs_review) return ' ✓'
    if (prop.needs_review) return ' ⚠'
    return ''
  }

  function handleEditValue() {
    if (!cellDetail) return
    setModalRefValue({
      ...cellDetail,
      element_system: matrix?.systems[expanded!.systemIdx].element_system,
      phase: matrix?.systems[expanded!.systemIdx].phase,
      property: expanded!.property,
    })
    setModalOpen(true)
  }

  if (loading || !profile) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-gray-400">加载中...</div>
      </div>
    )
  }

  if (profile.role !== 'admin') return null

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-[98vw] mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white">参考值矩阵</h1>
          <p className="text-gray-400 text-sm mt-1">14 体系 × 8 属性热力图总览</p>
        </div>

        {/* Legend */}
        <div className="flex gap-4 mb-4 text-xs flex-wrap">
          <span className="flex items-center gap-1">
            <span className="w-4 h-4 rounded bg-green-900/40 border border-green-800/40 inline-block" />
            <span className="text-green-300">高置信 ✓</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-4 rounded bg-yellow-900/40 border border-yellow-800/40 inline-block" />
            <span className="text-yellow-300">待审核 ⚠</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-4 rounded bg-red-900/40 border border-red-800/40 inline-block" />
            <span className="text-red-300">低置信 ⚠</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-4 rounded bg-gray-700 border border-gray-600 inline-block" />
            <span className="text-gray-500">已拒绝</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-4 rounded bg-gray-800 border border-gray-700 inline-block" />
            <span className="text-gray-600">缺失</span>
          </span>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* Matrix Table */}
        <div className="border border-gray-700 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              {/* Sticky header */}
              <thead className="sticky top-0 z-10">
                <tr className="bg-gray-700 text-gray-300 text-xs font-medium">
                  <th className="px-3 py-2 text-left border-r border-gray-600 sticky left-0 bg-gray-700 z-20 min-w-[120px]">
                    体系 / 相
                  </th>
                  {PROPERTIES.map(p => (
                    <th key={p} className="px-3 py-2 text-center whitespace-nowrap min-w-[90px]">
                      {PROPERTY_LABELS[p]}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {loadingData ? (
                  <tr>
                    <td colSpan={PROPERTIES.length + 1} className="text-center py-16 text-gray-500">
                      加载矩阵数据...
                    </td>
                  </tr>
                ) : matrix && matrix.systems.length > 0 ? (
                  matrix.systems.map((sys, idx) => {
                    const isExpanded = expanded?.systemIdx === idx
                    return (
                      <>
                        {/* Data row */}
                        <tr
                          key={sys.element_system + sys.phase}
                          className={`border-t border-gray-700/50 ${isExpanded ? 'bg-gray-700/20' : 'hover:bg-gray-800/60'}`}
                        >
                          <td className="px-3 py-2 text-sm font-medium text-white border-r border-gray-600 sticky left-0 bg-gray-900">
                            {sys.element_system}
                            {sys.phase ? (
                              <span className="text-gray-400 font-normal ml-1">({sys.phase})</span>
                            ) : null}
                          </td>
                          {PROPERTIES.map(p => {
                            const prop = sys.properties[p]
                            const isThisExpanded = isExpanded && expanded.property === p
                            return (
                              <td
                                key={p}
                                onClick={() => prop && prop.status !== 'rejected' && handleCellClick(idx, p)}
                                className={`px-3 py-2 text-sm text-center cursor-pointer transition ${getCellStyle(prop)} ${isThisExpanded ? 'ring-2 ring-blue-500 ring-inset' : 'hover:brightness-125'}`}
                                title={prop ? `${prop.value} ${prop.unit}` : '缺失'}
                              >
                                {prop ? (
                                  <>
                                    {typeof prop.value === 'number' ? prop.value.toPrecision(4) : prop.value}
                                    <span className="block text-[10px] text-gray-400/70">{prop.unit}</span>
                                  </>
                                ) : (
                                  <span className="text-gray-600">—</span>
                                )}
                              </td>
                            )
                          })}
                        </tr>

                        {/* Expanded detail row */}
                        {isExpanded && (
                          <tr key={`detail-${sys.element_system}-${sys.phase}`}>
                            <td colSpan={PROPERTIES.length + 1} className="p-0">
                              <div className="bg-gray-800/80 border-l-2 border-blue-500 px-4 py-3">
                                {detailLoading ? (
                                  <div className="text-gray-500 text-sm">加载详情...</div>
                                ) : cellDetail ? (
                                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                    <div>
                                      <span className="text-gray-400 text-xs">值</span>
                                      <div className="text-white font-medium">{cellDetail.value} {cellDetail.unit}</div>
                                    </div>
                                    <div>
                                      <span className="text-gray-400 text-xs">来源</span>
                                      <div className="text-gray-200">{cellDetail.source || '—'}</div>
                                    </div>
                                    <div>
                                      <span className="text-gray-400 text-xs">DOI</span>
                                      <div className="text-gray-200">{cellDetail.source_doi || '—'}</div>
                                    </div>
                                    <div>
                                      <span className="text-gray-400 text-xs">方法</span>
                                      <div className="text-gray-200">{cellDetail.method || '—'}</div>
                                    </div>
                                    <div>
                                      <span className="text-gray-400 text-xs">置信度</span>
                                      <div className={getCellStyle({
                                        confidence: cellDetail.confidence,
                                        needs_review: cellDetail.needs_review,
                                        status: cellDetail.status,
                                        value: 0, unit: '',
                                      }) + ' inline-block px-2 py-0.5 rounded text-xs'}>
                                        {cellDetail.confidence}{getStatusIcon({
                                          confidence: cellDetail.confidence,
                                          needs_review: cellDetail.needs_review,
                                          status: cellDetail.status,
                                          value: 0, unit: '',
                                        })}
                                      </div>
                                    </div>
                                    <div>
                                      <span className="text-gray-400 text-xs">审核状态</span>
                                      <div className="text-gray-200">{cellDetail.needs_review ? '待审核' : '已确认'} · {cellDetail.status}</div>
                                    </div>
                                    <div className="col-span-2 flex gap-2 items-end pt-1">
                                      <button className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition">
                                        补充来源
                                      </button>
                                      <button
                                        onClick={handleEditValue}
                                        className="px-3 py-1.5 text-xs rounded-lg bg-yellow-700 hover:bg-yellow-600 text-white transition"
                                      >
                                        修正值
                                      </button>
                                      <button className="px-3 py-1.5 text-xs rounded-lg bg-green-700 hover:bg-green-600 text-white transition">
                                        确认采纳
                                      </button>
                                      <button className="px-3 py-1.5 text-xs rounded-lg bg-red-800 hover:bg-red-700 text-white transition">
                                        拒绝
                                      </button>
                                      <button
                                        onClick={() => setExpanded(null)}
                                        className="px-3 py-1.5 text-xs rounded-lg border border-gray-600 text-gray-400 hover:text-white transition ml-auto"
                                      >
                                        收起
                                      </button>
                                    </div>
                                  </div>
                                ) : (
                                  <div className="text-gray-500 text-sm">暂无详情数据</div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    )
                  })
                ) : (
                  <tr>
                    <td colSpan={PROPERTIES.length + 1} className="text-center py-16 text-gray-500">
                      暂无矩阵数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Edit Value Modal */}
      {modalRefValue && (
        <EditValueModal
          isOpen={modalOpen}
          onClose={() => setModalOpen(false)}
          refValue={modalRefValue}
          sessionToken={session?.access_token ?? ''}
          onSuccess={fetchMatrix}
        />
      )}
    </div>
  )
}
