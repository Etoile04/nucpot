'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'

interface Potential {
  id: string
  name: string
  display_name: string
  type: string
  subtype: string
  format: string
  elements: string[]
  system_name: string
  system_tags: string[]
  description: string
  applicability: { temperatureRange?: number[]; phases?: string[]; notes?: string }
  references: { doi?: string; citation?: string; url?: string }[]
  developers: { name: string; affiliation?: string }[]
  verified_props: Record<string, unknown>
  sim_software: string[]
  lammps_config: { pair_style?: string; pair_coeff?: string; note?: string }
  file_url: string | null
  file_hash: string | null
  file_size: number | null
  source: string
  license: string
  tags: string[]
  extra: { irradiationRelevant?: boolean; hasDefectData?: boolean; hasLiquidPhase?: boolean; validationLevel?: string }
  created_at: string
  updated_at: string
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button onClick={handleCopy} className="text-xs text-blue-400 hover:text-blue-300 transition">
      {copied ? '已复制 ✓' : label}
    </button>
  )
}

function BibTeX({ ref_ }: { ref_: { citation?: string; doi?: string } }) {
  const bibtex = `@article{nucpot,
  title = {${ref_.citation || 'Unknown'}},
  doi = {${ref_.doi || ''}}
}`
  return <CopyButton text={bibtex} label="BibTeX" />
}

export default function PotentialDetailPage() {
  const { id } = useParams()
  const [potential, setPotential] = useState<Potential | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')

  useEffect(() => {
    fetch(`/api/potentials/${id}`)
      .then(r => r.json())
      .then(setPotential)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">加载中...</div>
  if (!potential) return <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">未找到势函数</div>

  const p = potential
  const tabs = [
    { id: 'overview', label: '概述' },
    { id: 'properties', label: '验证性质' },
    { id: 'citation', label: '引用' },
    { id: 'usage', label: '使用方法' },
  ]

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <Link href="/browse" className="text-sm text-gray-400 hover:text-white transition mb-4 inline-block">← 返回列表</Link>

        <h1 className="text-2xl font-bold mb-1">{p.display_name || p.name}</h1>
        <div className="text-gray-400 mb-4">{p.name}</div>

        <div className="flex flex-wrap gap-2 mb-6">
          <span className="px-3 py-1 bg-blue-900/50 rounded-full text-sm">{p.type}</span>
          <span className="px-3 py-1 bg-gray-700 rounded-full text-sm">{p.elements.join('-')}</span>
          {p.sim_software.map(s => (
            <span key={s} className="px-3 py-1 bg-gray-700 rounded-full text-sm">{s}</span>
          ))}
          {(p.system_tags || []).map(t => (
            <span key={t} className="px-3 py-1 bg-gray-700/50 rounded-full text-sm text-gray-300">{t}</span>
          ))}
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 border-b border-gray-700">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 text-sm transition border-b-2 ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-white'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === 'overview' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">描述</h3>
              <p className="text-gray-300">{p.description || '暂无描述'}</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">适用范围</h3>
                <div className="space-y-1 text-sm">
                  {p.applicability?.temperatureRange && (
                    <div>温度: {p.applicability.temperatureRange[0]} - {p.applicability.temperatureRange[1]} K</div>
                  )}
                  {p.applicability?.phases && (
                    <div>相态: {p.applicability.phases.join(', ')}</div>
                  )}
                  {p.applicability?.notes && (
                    <div className="text-gray-400">{p.applicability.notes}</div>
                  )}
                </div>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">分类标签</h3>
                <div className="flex flex-wrap gap-1">
                  {(p.tags || []).map(t => (
                    <span key={t} className="px-2 py-0.5 bg-gray-800 rounded text-xs">{t}</span>
                  ))}
                </div>
              </div>
            </div>
            {p.extra && (
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">核材料特性</h3>
                <div className="flex flex-wrap gap-2">
                  {p.extra.irradiationRelevant && <span className="px-2 py-1 bg-orange-900/30 rounded text-xs border border-orange-800">☢ 辐照相关</span>}
                  {p.extra.hasDefectData && <span className="px-2 py-1 bg-purple-900/30 rounded text-xs border border-purple-800">🔬 缺陷数据</span>}
                  {p.extra.hasLiquidPhase && <span className="px-2 py-1 bg-blue-900/30 rounded text-xs border border-blue-800">💧 液相数据</span>}
                  {p.extra.validationLevel && (
                    <span className="px-2 py-1 bg-green-900/30 rounded text-xs border border-green-800">
                      验证: {p.extra.validationLevel}
                    </span>
                  )}
                </div>
              </div>
            )}
            {p.developers && p.developers.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">开发者</h3>
                <div className="space-y-1 text-sm">
                  {p.developers.map((d, i) => (
                    <div key={i}>{d.name}{d.affiliation && <span className="text-gray-400"> — {d.affiliation}</span>}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'properties' && (
          <div>
            <h3 className="text-sm font-semibold text-gray-400 uppercase mb-4">验证性质</h3>
            {p.verified_props && Object.keys(p.verified_props).length > 0 ? (
              <div className="bg-gray-800/50 rounded-lg border border-gray-700 overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700">
                      <th className="px-4 py-2 text-left text-gray-400">性质</th>
                      <th className="px-4 py-2 text-left text-gray-400">值/状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(p.verified_props).map(([key, value]) => (
                      <tr key={key} className="border-b border-gray-700/50">
                        <td className="px-4 py-2 text-gray-300">{key}</td>
                        <td className="px-4 py-2">
                          <pre className="text-xs text-gray-400 whitespace-pre-wrap">
                            {JSON.stringify(value, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-gray-400">暂无验证性质数据</p>
            )}
          </div>
        )}

        {activeTab === 'citation' && (
          <div className="space-y-4">
            {p.references && p.references.length > 0 ? p.references.map((ref, i) => (
              <div key={i} className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-gray-300 mb-2">{ref.citation}</p>
                {ref.doi && (
                  <div className="text-sm mb-2">
                    DOI: <a href={`https://doi.org/${ref.doi}`} target="_blank" className="text-blue-400 hover:underline">{ref.doi}</a>
                  </div>
                )}
                <BibTeX ref_={ref} />
              </div>
            )) : (
              <p className="text-gray-400">暂无引用信息</p>
            )}
            {p.source && (
              <div className="text-sm text-gray-400">
                数据来源: {p.source}
              </div>
            )}
          </div>
        )}

        {activeTab === 'usage' && (
          <div className="space-y-6">
            {p.lammps_config?.pair_style && (
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">LAMMPS 命令</h3>
                <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 font-mono text-sm text-green-400">
                  <div>pair_style {p.lammps_config.pair_style}</div>
                  {p.lammps_config.pair_coeff && (
                    <div>pair_coeff {p.lammps_config.pair_coeff}</div>
                  )}
                </div>
                <div className="mt-2 flex gap-4">
                  <CopyButton
                    text={`pair_style ${p.lammps_config.pair_style}\npair_coeff ${p.lammps_config.pair_coeff || ''}`}
                    label="复制命令"
                  />
                </div>
                {p.lammps_config.note && (
                  <p className="text-sm text-yellow-400/80 mt-2">⚠️ {p.lammps_config.note}</p>
                )}
              </div>
            )}
            <div>
              <h3 className="text-sm font-semibold text-gray-400 uppercase mb-2">文件信息</h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700 text-sm space-y-2">
                {p.file_url ? (
                  <div>文件: <a href={p.file_url} className="text-blue-400 hover:underline">下载</a> {p.file_size && `(${(p.file_size / 1024).toFixed(1)} KB)`}</div>
                ) : (
                  <div>
                    <div className="text-yellow-400 mb-2">势函数文件需从原始来源获取</div>
                    <div className="space-y-1">
                      {p.source === 'NIST IPR' && (
                        <a
                          href={`https://www.ctcms.nist.gov/potentials/system/${p.elements.sort().join('-')}/`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-blue-400 hover:underline mr-4"
                        >
                          📦 NIST IPR 下载页
                        </a>
                      )}
                      {p.references?.[0]?.doi && (
                        <a
                          href={`https://doi.org/${p.references[0].doi}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-blue-400 hover:underline mr-4"
                        >
                          📄 原始论文
                        </a>
                      )}
                      <a
                        href="https://openkim.org"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-blue-400 hover:underline"
                      >
                        🔬 OpenKIM
                      </a>
                    </div>
                  </div>
                )}
                <div>格式: {p.format || '-'}</div>
                {p.file_hash && <div className="text-xs text-gray-500">SHA256: {p.file_hash}</div>}
                <div>来源: {p.source || '-'}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
