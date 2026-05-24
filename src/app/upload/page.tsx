'use client'

import { useState, FormEvent, useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

const POTENTIAL_TYPES = ['EAM', 'MEAM', 'Buckingham', 'Tersoff', 'AIREBO', 'LJ', 'other']

type LicenseType = 'own_work' | 'author_permission' | 'open_license' | ''

export default function UploadPage() {
  const router = useRouter()
  const { user, session, loading } = useAuth()
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  // License / Authorization fields
  const [licenseType, setLicenseType] = useState<LicenseType>('')
  const [licenseDetail, setLicenseDetail] = useState('')
  const [authFile, setAuthFile] = useState<File | null>(null)
  const [authFileUploading, setAuthFileUploading] = useState(false)

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

  async function uploadAuthFile(file: File): Promise<string | null> {
    setAuthFileUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('userId', user!.id)

      const res = await fetch('/api/auth/upload-proof', {
        method: 'POST',
        headers: { Authorization: `Bearer ${session!.access_token}` },
        body: formData,
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error || '授权文件上传失败')
        return null
      }
      return data.path
    } catch {
      setError('授权文件上传失败，请重试')
      return null
    } finally {
      setAuthFileUploading(false)
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)

    // Validate authorization
    if (!licenseType) {
      setError('请选择授权类型')
      return
    }
    if (licenseType === 'author_permission' && !authFile) {
      setError('作者授权情况下，请上传授权证明文件（PDF/图片）')
      return
    }
    if (licenseType === 'open_license' && !licenseDetail.trim()) {
      setError('请填写开源许可证名称（如 CC-BY-4.0、MIT 等）')
      return
    }

    setSubmitting(true)

    // Upload auth file if provided
    let authFilePath: string | null = null
    if (authFile && licenseType === 'author_permission') {
      authFilePath = await uploadAuthFile(authFile)
      if (!authFilePath) {
        setSubmitting(false)
        return
      }
    }

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
      // Authorization metadata
      license_type: licenseType,
      license_detail: licenseDetail.trim() || undefined,
      auth_file_path: authFilePath || undefined,
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
        setTags(''); setDoiRefs(''); setLicenseType('')
        setLicenseDetail(''); setAuthFile(null)
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

  const licenseOptions = [
    {
      value: 'own_work' as const,
      label: '原创作品',
      desc: '我是该势函数的作者或共同作者',
      needFile: false,
      needDetail: false,
    },
    {
      value: 'author_permission' as const,
      label: '作者授权',
      desc: '我已获得原始作者的书面授权，允许在此平台分发',
      needFile: true,
      needDetail: false,
    },
    {
      value: 'open_license' as const,
      label: '开源/公开许可证',
      desc: '该势函数已以开源许可证发布（如 CC-BY、MIT 等）',
      needFile: false,
      needDetail: true,
    },
  ]

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
          {/* ========== 授权声明（放在最前面） ========== */}
          <div className={`${sectionClass} border-2 border-yellow-600/50`}>
            <h2 className="text-base font-semibold text-yellow-400 border-b border-gray-700 pb-2 flex items-center gap-2">
              ⚖️ 版权授权声明 <span className="text-red-400 text-xs">（必填）</span>
            </h2>
            <p className="text-xs text-gray-400 leading-relaxed">
              势函数文件受原作者知识产权保护。请确认您有权在此平台分发该势函数。
              虚假声明可能导致内容下架和账号封禁。
            </p>

            <div className="space-y-3">
              {licenseOptions.map(opt => (
                <label
                  key={opt.value}
                  className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    licenseType === opt.value
                      ? 'bg-blue-900/30 border border-blue-500/50'
                      : 'bg-gray-800/50 border border-gray-700 hover:border-gray-600'
                  }`}
                >
                  <input
                    type="radio"
                    name="licenseType"
                    value={opt.value}
                    checked={licenseType === opt.value}
                    onChange={() => setLicenseType(opt.value)}
                    className="mt-1 accent-blue-500"
                  />
                  <div>
                    <div className="text-sm font-medium text-white">{opt.label}</div>
                    <div className="text-xs text-gray-400 mt-0.5">{opt.desc}</div>
                  </div>
                </label>
              ))}
            </div>

            {/* Author permission: upload proof */}
            {licenseType === 'author_permission' && (
              <div className="mt-4 space-y-2">
                <label className={labelClass}>
                  📎 上传授权证明文件 <span className="text-red-400">*</span>
                </label>
                <div className="flex items-center gap-3">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,.doc,.docx"
                    onChange={e => setAuthFile(e.target.files?.[0] || null)}
                    className="text-sm text-gray-300 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-600 file:text-white hover:file:bg-blue-500 file:cursor-pointer"
                  />
                  {authFileUploading && <span className="text-xs text-blue-400">上传中…</span>}
                </div>
                {authFile && (
                  <div className="text-xs text-green-400">
                    ✓ 已选择: {authFile.name} ({(authFile.size / 1024).toFixed(1)} KB)
                  </div>
                )}
                <div className="flex items-center gap-4 mt-2">
                  <a
                    href="/authorization-template.html"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-yellow-400 hover:text-yellow-300 hover:underline"
                  >
                    📥 下载授权书模板
                  </a>
                  <span className="text-xs text-gray-500">（打印 → 填写 → 签字 → 扫描上传）</span>
                </div>
                <p className="text-xs text-gray-500">
                  支持 PDF、PNG、JPG、DOC 格式。请上传作者签署的授权书、邮件授权截图等证明材料。
                </p>
              </div>
            )}

            {/* Open license: specify which */}
            {licenseType === 'open_license' && (
              <div className="mt-4">
                <label className={labelClass}>
                  许可证名称 <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={licenseDetail}
                  onChange={e => setLicenseDetail(e.target.value)}
                  placeholder="e.g. CC-BY-4.0, MIT, GPL-3.0"
                  className={inputClass}
                />
                <p className="text-xs text-gray-500 mt-1">
                  请填写该势函数发布的开源许可证全称。如果是 NIST IPR 公开资源，可填写 "NIST IPR Public"。
                </p>
              </div>
            )}
          </div>

          {/* ========== 基本信息 ========== */}
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

          {/* ========== 适用条件 ========== */}
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

          {/* ========== LAMMPS 配置 ========== */}
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

          {/* ========== 元数据 ========== */}
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

          {/* ========== 提交 ========== */}
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
              disabled={submitting || authFileUploading}
              className={
                'px-6 py-2 rounded-lg font-medium transition-colors ' +
                (submitting || authFileUploading
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
