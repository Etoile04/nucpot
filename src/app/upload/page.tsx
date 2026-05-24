'use client'

import { useState, FormEvent, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

const POTENTIAL_TYPES = ['EAM', 'MEAM', 'Buckingham', 'Tersoff', 'AIREBO', 'LJ', 'other']

export default function UploadPage() {
  const router = useRouter()
  const { user, session, loading } = useAuth()

  // Form fields
  const [name, setName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [type, setType] = useState('')
  const [subtype, setSubtype] = useState('')
  const [format, setFormat] = useState('LAMMPS')
  const [elements, setElements] = useState('')
  const [systemName, setSystemName] = useState('')
  const [systemTags, setSystemTags] = useState('')
  const [description, setDescription] = useState('')
  const [tempRange, setTempRange] = useState('')
  const [phases, setPhases] = useState('')
  const [pairStyle, setPairStyle] = useState('')
  const [pairCoeff, setPairCoeff] = useState('')
  const [tags, setTags] = useState('')
  const [doiRefs, setDoiRefs] = useState('')

  // UI state
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Redirect if not authenticated
  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [loading, user, router])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400">Loading...</div>
      </div>
    )
  }

  if (!user) return null

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setSubmitting(true)

    // Parse comma-separated fields
    const elementsArray = elements.split(',').map(s => s.trim()).filter(Boolean)
    const systemTagsArray = systemTags.split(',').map(s => s.trim()).filter(Boolean)
    const tagsArray = tags.split(',').map(s => s.trim()).filter(Boolean)
    const referencesArray = doiRefs.split(',').map(s => s.trim()).filter(Boolean)
      .map(doi => ({ doi }))

    const body: Record<string, unknown> = {
      name: name.trim(),
      display_name: displayName.trim() || undefined,
      type,
      subtype: subtype.trim() || undefined,
      format: format.trim() || 'LAMMPS',
      elements: elementsArray,
      system_name: systemName.trim(),
      system_tags: systemTagsArray,
      description: description.trim(),
      tags: tagsArray,
      references: referencesArray,
    }

    // Applicability
    const applicability: Record<string, unknown> = {}
    if (tempRange.trim()) applicability.temperatureRange = tempRange.trim()
    if (phases.trim()) {
      applicability.phases = phases.split(',').map(s => s.trim()).filter(Boolean)
    }
    if (Object.keys(applicability).length > 0) {
      body.applicability = applicability
    }

    // LAMMPS config
    const lammpsConfig: Record<string, string> = {}
    if (pairStyle.trim()) lammpsConfig.pair_style = pairStyle.trim()
    if (pairCoeff.trim()) lammpsConfig.pair_coeff = pairCoeff.trim()
    if (Object.keys(lammpsConfig).length > 0) {
      body.lammps_config = lammpsConfig
    }

    try {
      const res = await fetch('/api/potentials/upload', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session!.access_token}`,
        },
        body: JSON.stringify(body),
      })

      const data = await res.json()

      if (!res.ok) {
        setError(data.error || '上传失败，请稍后重试')
      } else {
        setSuccess(`势函数 "${data.potential.name}" 已提交，等待管理员审核。`)
        // Reset form
        setName(''); setDisplayName(''); setType(''); setSubtype('')
        setFormat('LAMMPS'); setElements(''); setSystemName('')
        setSystemTags(''); setDescription(''); setTempRange('')
        setPhases(''); setPairStyle(''); setPairCoeff('')
        setTags(''); setDoiRefs('')
      }
    } catch {
      setError('网络错误，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 ' +
    'placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 ' +
    'transition-colors'
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1'
  const sectionClass = 'bg-gray-900 rounded-xl p-6 space-y-4'

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white">上传势函数</h1>
          <p className="mt-1 text-gray-400 text-sm">
            提交您的势函数供社区使用，管理员审核通过后将公开展示。
          </p>
        </div>

        {/* Success / Error banners */}
        {success && (
          <div className="mb-6 rounded-lg bg-green-900/40 border border-green-700 px-4 py-3 text-green-300 text-sm">
            ✓ {success}
          </div>
        )}
        {error && (
          <div className="mb-6 rounded-lg bg-red-900/40 border border-red-700 px-4 py-3 text-red-300 text-sm">
            ✗ {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Basic Info */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              基本信息
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>
                  势函数名称 <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="e.g. Fe_EAM_Mendelev2003"
                  className={inputClass}
                  required
                />
              </div>

              <div>
                <label className={labelClass}>显示名称</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  placeholder="留空则使用势函数名称"
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>
                  类型 <span className="text-red-400">*</span>
                </label>
                <select
                  value={type}
                  onChange={e => setType(e.target.value)}
                  className={inputClass}
                  required
                >
                  <option value="">请选择类型</option>
                  {POTENTIAL_TYPES.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className={labelClass}>子类型</label>
                <input
                  type="text"
                  value={subtype}
                  onChange={e => setSubtype(e.target.value)}
                  placeholder="e.g. fs, alloy"
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>格式</label>
                <input
                  type="text"
                  value={format}
                  onChange={e => setFormat(e.target.value)}
                  placeholder="e.g. LAMMPS"
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>
                  元素（逗号分隔） <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={elements}
                  onChange={e => setElements(e.target.value)}
                  placeholder="e.g. Fe, Ni, Cr"
                  className={inputClass}
                  required
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>
                  体系名称 <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={systemName}
                  onChange={e => setSystemName(e.target.value)}
                  placeholder="e.g. Fe-Ni 合金"
                  className={inputClass}
                  required
                />
              </div>

              <div>
                <label className={labelClass}>体系标签（逗号分隔）</label>
                <input
                  type="text"
                  value={systemTags}
                  onChange={e => setSystemTags(e.target.value)}
                  placeholder="e.g. steel, alloy"
                  className={inputClass}
                />
              </div>
            </div>

            <div>
              <label className={labelClass}>
                描述 <span className="text-red-400">*</span>
              </label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={4}
                placeholder="请描述该势函数的适用范围、来源、验证情况等"
                className={inputClass}
                required
              />
            </div>
          </div>

          {/* Applicability */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              适用条件（可选）
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>温度范围</label>
                <input
                  type="text"
                  value={tempRange}
                  onChange={e => setTempRange(e.target.value)}
                  placeholder="e.g. 300-1200 K"
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>相（逗号分隔）</label>
                <input
                  type="text"
                  value={phases}
                  onChange={e => setPhases(e.target.value)}
                  placeholder="e.g. fcc, bcc, liquid"
                  className={inputClass}
                />
              </div>
            </div>
          </div>

          {/* LAMMPS Config */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              LAMMPS 配置（可选）
            </h2>

            <div>
              <label className={labelClass}>pair_style</label>
              <input
                type="text"
                value={pairStyle}
                onChange={e => setPairStyle(e.target.value)}
                placeholder="e.g. eam/fs"
                className={inputClass}
              />
            </div>

            <div>
              <label className={labelClass}>pair_coeff</label>
              <input
                type="text"
                value={pairCoeff}
                onChange={e => setPairCoeff(e.target.value)}
                placeholder="e.g. * * Fe.eam.fs Fe"
                className={inputClass}
              />
            </div>
          </div>

          {/* Metadata */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              元数据（可选）
            </h2>

            <div>
              <label className={labelClass}>标签（逗号分隔）</label>
              <input
                type="text"
                value={tags}
                onChange={e => setTags(e.target.value)}
                placeholder="e.g. radiation, defect, diffusion"
                className={inputClass}
              />
            </div>

            <div>
              <label className={labelClass}>参考文献 DOI（逗号分隔）</label>
              <input
                type="text"
                value={doiRefs}
                onChange={e => setDoiRefs(e.target.value)}
                placeholder="e.g. 10.1103/PhysRevB.68.024102"
                className={inputClass}
              />
            </div>
          </div>

          {/* Submit */}
          <div className="flex items-center justify-end gap-4 pt-2">
            <button
              type="button"
              onClick={() => router.back()}
              className="px-5 py-2 rounded-lg text-gray-300 hover:text-white hover:bg-gray-800 transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={
                'px-6 py-2 rounded-lg font-medium transition-colors ' +
                (submitting
                  ? 'bg-blue-800 text-blue-300 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-500 text-white')
              }
            >
              {submitting ? '提交中…' : '提交审核'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
