'use client'

import { useState, useEffect, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import type { Potential } from '@/lib/types'

function CompareContent() {
  const searchParams = useSearchParams()
  const ids = searchParams.get('ids')?.split(',').filter(Boolean) || []

  const [potentials, setPotentials] = useState<Potential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (ids.length === 0) {
      setLoading(false)
      return
    }

    setLoading(true)
    Promise.all(
      ids.map(id =>
        fetch(`/api/potentials/${id}`)
          .then(r => {
            if (!r.ok) throw new Error(`势函数 ${id} 不存在`)
            return r.json()
          })
      )
    )
      .then(results => {
        setPotentials(results)
        setError('')
      })
      .catch(e => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [searchParams.get('ids')])

  if (ids.length < 2) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <div className="text-5xl mb-4">⚖️</div>
          <h1 className="text-xl font-semibold mb-2">势函数对比</h1>
          <p className="text-gray-400 mb-4">请至少选择 2 个势函数进行对比</p>
          <Link
            href="/browse"
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm transition"
          >
            前往浏览
          </Link>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        加载中...
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-400 mb-4">{error}</p>
          <Link href="/browse" className="text-blue-400 hover:text-blue-300">
            返回浏览
          </Link>
        </div>
      </div>
    )
  }

  type RowDef = { label: string; render: (p: Potential) => React.ReactNode }

  const sections: { title: string; rows: RowDef[] }[] = [
    {
      title: '基本信息',
      rows: [
        { label: '名称', render: p => <span className="font-semibold text-blue-300">{p.display_name || p.name}</span> },
        { label: '内部名称', render: p => <span className="text-gray-300 font-mono text-xs">{p.name}</span> },
        { label: '类型', render: p => <span className="px-2 py-0.5 bg-blue-900/50 rounded text-xs">{p.type}</span> },
        { label: '元素', render: p => <span>{p.elements.join('-')}</span> },
        { label: '体系', render: p => p.system_name || '—' },
        { label: '描述', render: p => <span className="text-sm text-gray-400 line-clamp-3">{p.description || '—'}</span> },
      ],
    },
    {
      title: '适用性',
      rows: [
        {
          label: '温度范围',
          render: p =>
            p.applicability?.temperatureRange
              ? `${p.applicability.temperatureRange[0]} – ${p.applicability.temperatureRange[1]} K`
              : '—',
        },
        {
          label: '相态',
          render: p =>
            p.applicability?.phases?.length
              ? p.applicability.phases.join(', ')
              : '—',
        },
        {
          label: '适用性备注',
          render: p => p.applicability?.notes || '—',
        },
      ],
    },
    {
      title: '验证与文件',
      rows: [
        {
          label: '验证等级',
          render: p => {
            const lvl = p.extra?.validationLevel || 'basic'
            const color = lvl === 'production' ? 'text-green-400' : lvl === 'benchmarked' ? 'text-yellow-400' : 'text-gray-400'
            return <span className={color}>{lvl}</span>
          },
        },
        { label: '文件可用', render: p => p.file_url ? '✅ 是' : '❌ 否' },
        { label: '文件格式', render: p => p.format || '—' },
        { label: 'LAMMPS pair_style', render: p => p.lammps_config?.pair_style || '—' },
      ],
    },
    {
      title: '核材料特性',
      rows: [
        { label: '辐照相关', render: p => p.extra?.irradiationRelevant ? '✅ 是' : '—' },
        { label: '缺陷数据', render: p => p.extra?.hasDefectData ? '✅ 是' : '—' },
        { label: '液相数据', render: p => p.extra?.hasLiquidPhase ? '✅ 是' : '—' },
      ],
    },
    {
      title: '开发者与引用',
      rows: [
        {
          label: '开发者',
          render: p =>
            p.developers?.length
              ? p.developers.map(d => d.name).join(', ')
              : '—',
        },
        {
          label: '引用文献',
          render: p =>
            p.references?.length ? (
              <ul className="space-y-1">
                {p.references.map((ref, i) => (
                  <li key={i} className="text-xs">
                    {ref.citation || ref.doi || '—'}
                    {ref.doi && (
                      <a
                        href={`https://doi.org/${ref.doi}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:text-blue-300 ml-1"
                      >
                        [DOI]
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              '—'
            ),
        },
      ],
    },
  ]

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">势函数对比</h1>
            <p className="text-sm text-gray-400 mt-1">
              对比 {potentials.length} 个势函数的详细信息
            </p>
          </div>
          <Link
            href="/browse"
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-sm transition"
          >
            ← 返回浏览
          </Link>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse min-w-[600px]">
            {/* Header row with potential names */}
            <thead>
              <tr className="border-b border-gray-700">
                <th className="sticky left-0 bg-gray-900 z-10 text-left p-3 w-40 text-sm font-medium text-gray-400">
                  属性
                </th>
                {potentials.map(p => (
                  <th
                    key={p.id}
                    className="text-center p-3 text-sm font-semibold min-w-[200px]"
                  >
                    <Link
                      href={`/potential/${p.id}`}
                      className="text-blue-400 hover:text-blue-300 transition"
                    >
                      {p.display_name || p.name}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sections.map(section => (
                <>
                  <tr key={`section-${section.title}`}>
                    <td
                      colSpan={potentials.length + 1}
                      className="bg-gray-800/50 px-3 py-2 text-sm font-semibold text-blue-400 border-t border-gray-700"
                    >
                      {section.title}
                    </td>
                  </tr>
                  {section.rows.map(row => (
                    <tr
                      key={`${section.title}-${row.label}`}
                      className="border-b border-gray-800 hover:bg-gray-800/30 transition"
                    >
                      <td className="sticky left-0 bg-gray-900 z-10 p-3 text-sm text-gray-400">
                        {row.label}
                      </td>
                      {potentials.map(p => (
                        <td key={p.id} className="p-3 text-sm text-center">
                          {row.render(p)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
          加载中...
        </div>
      }
    >
      <CompareContent />
    </Suspense>
  )
}
