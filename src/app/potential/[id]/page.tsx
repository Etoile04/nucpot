'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import VerificationPanel from '@/components/VerificationPanel'

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
  verified_props: Record<string, unknown> | null
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

const PROPERTY_LABELS: Record<string, string> = {
  lattice_constant: '晶格常数',
  lattice_constants: '晶格常数',
  elastic_constants: '弹性常数',
  c11: 'C₁₁',
  c12: 'C₁₂',
  c44: 'C₄₄',
  bulk_modulus: '体积模量',
  shear_modulus: '剪切模量',
  youngs_modulus: '杨氏模量',
  poisson_ratio: '泊松比',
  formation_energy: '形成能',
  cohesive_energy: '结合能',
  melting_point: '熔点',
  surface_energy: '表面能',
  stacking_fault_energy: '层错能',
  vacancy_formation_energy: '空位形成能',
  interstitial_formation_energy: '间隙形成能',
  thermal_expansion: '热膨胀系数',
  specific_heat: '比热容',
  thermal_conductivity: '热导率',
  density: '密度',
}

function getLabel(key: string): string {
  return PROPERTY_LABELS[key] || key
}

function extractNumeric(v: unknown): number | null {
  if (typeof v === 'number') return v
  if (typeof v === 'string') {
    const m = v.match(/-?[\d.]+(?:e[+-]?\d+)?/i)
    return m ? parseFloat(m[0]) : null
  }
  return null
}

function deviationColor(pct: number): string {
  if (Math.abs(pct) < 5) return 'text-green-400'
  if (Math.abs(pct) < 10) return 'text-yellow-400'
  return 'text-red-400'
}

interface PropEntry {
  label: string
  key: string
  value: string
  unit: string
  refValue: string | null
  deviation: string | null
  deviationClass: string
}

function parseVerifiedProps(props: Record<string, unknown>): PropEntry[] {
  return Object.entries(props).map(([key, raw]) => {
    const label = getLabel(key)

    // If raw is a flat number/string, simple entry
    if (typeof raw === 'number' || typeof raw === 'string') {
      return { label, key, value: String(raw), unit: '', refValue: null, deviation: null, deviationClass: '' }
    }

    // If raw is an object, extract fields
    if (raw && typeof raw === 'object') {
      const obj = raw as Record<string, unknown>
      const computed = obj.computed ?? obj.calculated ?? obj.value ?? obj.result
      const unit = obj.unit ? String(obj.unit) : ''
      const ref = obj.experimental ?? obj.experimental_data ?? obj.reference ?? obj.reference_value
      const refNum = ref != null ? extractNumeric(ref) : null
      const compNum = extractNumeric(computed)

      let deviation: string | null = null
      let deviationClass = ''
      if (refNum != null && compNum != null && refNum !== 0) {
        const pct = ((compNum - refNum) / Math.abs(refNum)) * 100
        deviation = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
        deviationClass = deviationColor(pct)
      }

      return {
        label,
        key,
        value: computed != null ? String(computed) : JSON.stringify(raw),
        unit,
        refValue: ref != null ? String(ref) : null,
        deviation,
        deviationClass,
      }
    }

    return { label, key, value: JSON.stringify(raw), unit: '', refValue: null, deviation: null, deviationClass: '' }
  })
}

function LAMMPSScriptTemplate({ potential: p }: { potential: Potential }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!p.lammps_config?.pair_style) return null

  const temperatureRange = p.applicability?.temperatureRange
  const exampleTemp = temperatureRange
    ? Math.round((temperatureRange[0] + temperatureRange[1]) / 2)
    : 300

  const displayName = p.display_name || p.name
  const pairStyle = p.lammps_config.pair_style
  const pairCoeff = p.lammps_config.pair_coeff || ''

  const script = `# LAMMPS 输入脚本 — ${displayName}
# 自动生成 by NucPot，请根据实际模拟需求修改

units           metal
dimension       3
boundary        p p p
atom_style      atomic

# 读取原子模型文件（需自行准备）
read_data       model.data

# 势函数设置
pair_style      ${pairStyle}
pair_coeff      ${pairCoeff}

# 邻居列表
neighbor        2.0 bin
neigh_modify    every 1 delay 0 check yes

# 时间步长（建议值，请根据体系调整）
timestep        0.001

# 能量最小化示例
minimize        1e-10 1e-10 1000 10000

# 或 MD 模拟示例
# velocity      all create ${exampleTemp} 87287 dist gaussian
# fix           1 all npt temp ${exampleTemp} ${exampleTemp} 0.1 iso 0 0 1
# thermo        100
# thermo_style  custom step temp pe ke etotal press vol
# run           10000
`

  const handleCopy = () => {
    navigator.clipboard.writeText(script)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm font-semibold text-gray-400 uppercase hover:text-gray-200 transition"
      >
        <span className={`transform transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        完整 LAMMPS 输入脚本模板
      </button>
      {expanded && (
        <div className="mt-3">
          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 font-mono text-sm text-green-400 whitespace-pre-wrap">{script}</div>
          <div className="mt-2 flex items-center gap-4">
            <button
              onClick={handleCopy}
              className="text-xs text-blue-400 hover:text-blue-300 transition"
            >
              {copied ? '已复制 ✓' : '一键复制完整脚本'}
            </button>
            <span className="text-xs text-yellow-400/80">⚠️ 此脚本为模板，请根据实际模拟需求修改参数</span>
          </div>
        </div>
      )}
    </div>
  )
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
    { id: 'verify', label: '验证' },
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

        {activeTab === 'properties' && (() => {
          if (!p.verified_props || Object.keys(p.verified_props).length === 0) {
            return <p className="text-gray-400">暂无验证性质数据</p>
          }
          const entries = parseVerifiedProps(p.verified_props)
          const hasRef = entries.some(e => e.refValue != null)
          return (
            <div>
              <h3 className="text-sm font-semibold text-gray-400 uppercase mb-4">验证性质</h3>
              <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-700 sticky top-0">
                      <th className="px-4 py-2 text-left text-gray-300">性质名称</th>
                      <th className="px-4 py-2 text-left text-gray-300">计算值</th>
                      <th className="px-4 py-2 text-left text-gray-300">单位</th>
                      {hasRef && <th className="px-4 py-2 text-left text-gray-300">实验参考值</th>}
                      {hasRef && <th className="px-4 py-2 text-left text-gray-300">偏差</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(e => (
                      <tr key={e.key} className="border-b border-gray-700/50 hover:bg-gray-700/50 transition">
                        <td className="px-4 py-2 text-gray-200">{e.label}</td>
                        <td className="px-4 py-2 text-gray-300 font-mono">{e.value}</td>
                        <td className="px-4 py-2 text-gray-400">{e.unit}</td>
                        {hasRef && <td className="px-4 py-2 text-gray-400">{e.refValue ?? '-'}</td>}
                        {hasRef && <td className={`px-4 py-2 font-mono ${e.deviationClass || 'text-gray-500'}`}>{e.deviation ?? '-'}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })()}

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
            <LAMMPSScriptTemplate potential={p} />
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

        {activeTab === 'verify' && (
          <VerificationPanel potentialName={p.name} />
        )}
      </div>
    </div>
  )
}
