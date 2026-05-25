'use client'

import { useState, FormEvent, useEffect, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

const POTENTIAL_TYPES = ['EAM', 'MEAM', 'Buckingham', 'Tersoff', 'AIREBO', 'LJ', 'other']
const DRAFT_KEY = 'nucpot_upload_draft'

type LicenseType = 'own_work' | 'author_permission' | 'open_license' | ''

interface DraftData {
  name: string; displayName: string; type: string; subtype: string; format: string;
  elements: string; systemName: string; systemTags: string; description: string;
  tempRange: string; phases: string; pairStyle: string; pairCoeff: string;
  tags: string; doiRefs: string; licenseType: LicenseType; licenseDetail: string;
}

function getEmptyDraft(): DraftData {
  return {
    name: '', displayName: '', type: '', subtype: '', format: 'LAMMPS',
    elements: '', systemName: '', systemTags: '', description: '',
    tempRange: '', phases: '', pairStyle: '', pairCoeff: '',
    tags: '', doiRefs: '', licenseType: '', licenseDetail: '',
  }
}

function loadDraft(): DraftData | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const d = JSON.parse(raw)
    // Validate it has expected fields
    if (d && typeof d.name === 'string') return d
  } catch { /* ignore */ }
  return null
}

function saveDraft(d: DraftData) {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify(d)) } catch { /* ignore */ }
}

function clearDraft() {
  try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore */ }
}

export default function UploadPage() {
  const router = useRouter()
  const { user, session, loading } = useAuth()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Load draft or empty
  const [draft] = useState<DraftData>(() => loadDraft() || getEmptyDraft())
  const [hasDraft] = useState(() => loadDraft() !== null)

  // Form fields — initialized from draft
  const [name, setName] = useState(draft.name)
  const [displayName, setDisplayName] = useState(draft.displayName)
  const [type, setType] = useState(draft.type)
  const [subtype, setSubtype] = useState(draft.subtype)
  const [format, setFormat] = useState(draft.format)
  const [elements, setElements] = useState(draft.elements)
  const [systemName, setSystemName] = useState(draft.systemName)
  const [systemTags, setSystemTags] = useState(draft.systemTags)
  const [description, setDescription] = useState(draft.description)
  const [tempRange, setTempRange] = useState(draft.tempRange)
  const [phases, setPhases] = useState(draft.phases)
  const [pairStyle, setPairStyle] = useState(draft.pairStyle)
  const [pairCoeff, setPairCoeff] = useState(draft.pairCoeff)
  const [tags, setTags] = useState(draft.tags)
  const [doiRefs, setDoiRefs] = useState(draft.doiRefs)
  const [licenseType, setLicenseType] = useState<LicenseType>(draft.licenseType)
  const [licenseDetail, setLicenseDetail] = useState(draft.licenseDetail)

  // File fields — not persisted in draft (File objects can't be serialized)
  const [potentialFile, setPotentialFile] = useState<File | null>(null)
  const [authFile, setAuthFile] = useState<File | null>(null)
  const [authFileUploading, setAuthFileUploading] = useState(false)

  // UI state
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastSaved, setLastSaved] = useState<Date | null>(hasDraft ? new Date() : null)

  // Auto-save draft on field changes (debounced)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const autoSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      saveDraft({
        name, displayName, type, subtype, format, elements, systemName, systemTags,
        description, tempRange, phases, pairStyle, pairCoeff, tags, doiRefs, licenseType, licenseDetail,
      })
      setLastSaved(new Date())
    }, 1000)
  }, [name, displayName, type, subtype, format, elements, systemName, systemTags,
      description, tempRange, phases, pairStyle, pairCoeff, tags, doiRefs, licenseType, licenseDetail])

  // Save on any field change
  useEffect(() => {
    // Don't auto-save if form is empty (initial state)
    if (name || displayName || type || elements || description || licenseType) {
      autoSave()
    }
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [autoSave, name, displayName, type, elements, description, licenseType])

  // Save on page unload
  useEffect(() => {
    const handler = () => {
      saveDraft({
        name, displayName, type, subtype, format, elements, systemName, systemTags,
        description, tempRange, phases, pairStyle, pairCoeff, tags, doiRefs, licenseType, licenseDetail,
      })
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [name, displayName, type, subtype, format, elements, systemName, systemTags,
      description, tempRange, phases, pairStyle, pairCoeff, tags, doiRefs, licenseType, licenseDetail])

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

  function openTemplate(lang: 'zh' | 'en', autoPrint: boolean) {
    // Fetch user profile for name/affiliation
    const userName = user?.user_metadata?.full_name || user?.email?.split('@')[0] || ''
    const userEmail = user?.email || ''
    const params = new URLSearchParams({
      lang,
      print: autoPrint ? '1' : '0',
      name, type, elements, systemName, doiRefs,
      userName, userEmail,
      userId: user?.id || '',
    })
    const url = `/api/auth/template?${params.toString()}`
    const w = window.open('', '_blank')
    if (w) {
      w.document.write('<html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;color:#666">加载中…</body></html>')
      w.document.close()
      fetch(url)
        .then(r => r.json())
        .then(data => {
          if (data.html) { w.document.open(); w.document.write(data.html); w.document.close() }
        })
        .catch(() => { w.document.body.textContent = '加载失败，请重试' })
    }
  }

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
      if (!res.ok) { setError(data.error || '授权文件上传失败'); return null }
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

    if (!licenseType) { setError('请选择授权类型'); return }
    if (licenseType === 'author_permission' && !authFile) { setError('作者授权情况下，请上传授权证明文件'); return }
    if (licenseType === 'open_license' && !licenseDetail.trim()) { setError('请填写开源许可证名称'); return }

    setSubmitting(true)

    let authFilePath: string | null = null
    if (authFile && licenseType === 'author_permission') {
      authFilePath = await uploadAuthFile(authFile)
      if (!authFilePath) { setSubmitting(false); return }
    }

    let potentialFileUrl: string | null = null
    if (potentialFile) {
      try {
        const formData = new FormData()
        formData.append('file', potentialFile)
        formData.append('userId', user!.id)
        formData.append('type', 'potential')
        const uploadRes = await fetch('/api/auth/upload-proof', {
          method: 'POST',
          headers: { Authorization: `Bearer ${session!.access_token}` },
          body: formData,
        })
        const uploadData = await uploadRes.json()
        if (uploadRes.ok && uploadData.path) potentialFileUrl = uploadData.path
      } catch { /* non-blocking */ }
    }

    const elementsArray = elements.split(',').map(s => s.trim()).filter(Boolean)
    const systemTagsArray = systemTags.split(',').map(s => s.trim()).filter(Boolean)
    const tagsArray = tags.split(',').map(s => s.trim()).filter(Boolean)
    const referencesArray = doiRefs.split(',').map(s => s.trim()).filter(Boolean).map(doi => ({ doi }))

    const body: Record<string, unknown> = {
      name: name.trim(), display_name: displayName.trim() || undefined,
      type, subtype: subtype.trim() || undefined, format: format.trim() || 'LAMMPS',
      elements: elementsArray, system_name: systemName.trim(), system_tags: systemTagsArray,
      description: description.trim(), tags: tagsArray, references: referencesArray,
      license_type: licenseType, license_detail: licenseDetail.trim() || undefined,
      auth_file_path: authFilePath || undefined, file_url: potentialFileUrl || undefined,
    }

    const applicability: Record<string, unknown> = {}
    if (tempRange.trim()) applicability.temperatureRange = tempRange.trim()
    if (phases.trim()) applicability.phases = phases.split(',').map(s => s.trim()).filter(Boolean)
    if (Object.keys(applicability).length > 0) body.applicability = applicability

    const lammpsConfig: Record<string, string> = {}
    if (pairStyle.trim()) lammpsConfig.pair_style = pairStyle.trim()
    if (pairCoeff.trim()) lammpsConfig.pair_coeff = pairCoeff.trim()
    if (Object.keys(lammpsConfig).length > 0) body.lammps_config = lammpsConfig

    try {
      const res = await fetch('/api/potentials/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session!.access_token}` },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error || '上传失败，请稍后重试')
      } else {
        setSuccess(`势函数 "${data.potential.name}" 已提交，等待管理员审核。`)
        // Reset all
        setName(''); setDisplayName(''); setType(''); setSubtype('')
        setFormat('LAMMPS'); setElements(''); setSystemName('')
        setSystemTags(''); setDescription(''); setTempRange('')
        setPhases(''); setPairStyle(''); setPairCoeff('')
        setTags(''); setDoiRefs(''); setLicenseType('')
        setLicenseDetail(''); setAuthFile(null); setPotentialFile(null)
        clearDraft()
        setLastSaved(null)
      }
    } catch {
      setError('网络错误，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 ' +
    'placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors'
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1'
  const sectionClass = 'bg-gray-900 rounded-xl p-6 space-y-4'

  const licenseOptions = [
    { value: 'own_work' as const, label: '原创作品', desc: '我是该势函数的作者或共同作者', needFile: false, needDetail: false },
    { value: 'author_permission' as const, label: '作者授权', desc: '我已获得原始作者的书面授权，允许在此平台分发', needFile: true, needDetail: false },
    { value: 'open_license' as const, label: '开源/公开许可证', desc: '该势函数已以开源许可证发布（如 CC-BY、MIT 等）', needFile: false, needDetail: true },
  ]

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white">上传势函数</h1>
          <p className="mt-1 text-gray-400 text-sm">
            提交您的势函数供社区使用，管理员审核通过后将公开展示。
          </p>
        </div>

        {/* Draft indicator */}
        {lastSaved && (
          <div className="mb-4 flex items-center justify-between rounded-lg bg-gray-800/50 border border-gray-700 px-4 py-2">
            <span className="text-xs text-gray-400">
              💾 表单已自动暂存于 {lastSaved.toLocaleTimeString('zh-CN')}，关闭页面后数据不会丢失
            </span>
            <button
              type="button"
              onClick={() => {
                const empty = getEmptyDraft()
                setName(empty.name); setDisplayName(empty.displayName); setType(empty.type)
                setSubtype(empty.subtype); setFormat(empty.format); setElements(empty.elements)
                setSystemName(empty.systemName); setSystemTags(empty.systemTags)
                setDescription(empty.description); setTempRange(empty.tempRange)
                setPhases(empty.phases); setPairStyle(empty.pairStyle); setPairCoeff(empty.pairCoeff)
                setTags(empty.tags); setDoiRefs(empty.doiRefs); setLicenseType(empty.licenseType)
                setLicenseDetail(empty.licenseDetail); clearDraft(); setLastSaved(null)
              }}
              className="text-xs text-red-400 hover:text-red-300"
            >
              清空暂存
            </button>
          </div>
        )}

        {/* Success / Error */}
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

          {/* ========== 基本信息 ========== */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              基本信息
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>势函数名称 <span className="text-red-400">*</span></label>
                <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Fe_EAM_Mendelev2003" className={inputClass} required />
              </div>
              <div>
                <label className={labelClass}>显示名称</label>
                <input type="text" value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="留空则使用势函数名称" className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>类型 <span className="text-red-400">*</span></label>
                <select value={type} onChange={e => setType(e.target.value)} className={inputClass} required>
                  <option value="">请选择类型</option>
                  {POTENTIAL_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>子类型</label>
                <input type="text" value={subtype} onChange={e => setSubtype(e.target.value)} placeholder="e.g. fs, alloy" className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>格式</label>
                <input type="text" value={format} onChange={e => setFormat(e.target.value)} placeholder="e.g. LAMMPS" className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>元素（逗号分隔） <span className="text-red-400">*</span></label>
                <input type="text" value={elements} onChange={e => setElements(e.target.value)} placeholder="e.g. Fe, Ni, Cr" className={inputClass} required />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>体系名称 <span className="text-red-400">*</span></label>
                <input type="text" value={systemName} onChange={e => setSystemName(e.target.value)} placeholder="e.g. Fe-Ni 合金" className={inputClass} required />
              </div>
              <div>
                <label className={labelClass}>体系标签（逗号分隔）</label>
                <input type="text" value={systemTags} onChange={e => setSystemTags(e.target.value)} placeholder="e.g. steel, alloy" className={inputClass} />
              </div>
            </div>
            <div>
              <label className={labelClass}>描述 <span className="text-red-400">*</span></label>
              <textarea value={description} onChange={e => setDescription(e.target.value)} rows={4} placeholder="请描述该势函数的适用范围、来源、验证情况等" className={inputClass} required />
            </div>
          </div>

          {/* ========== 版权授权 ========== */}
          <div className={`${sectionClass} border-2 border-yellow-600/50`}>
            <h2 className="text-base font-semibold text-yellow-400 border-b border-gray-700 pb-2 flex items-center gap-2">
              ⚖️ 版权授权声明 <span className="text-red-400 text-xs">（必填）</span>
            </h2>
            <p className="text-xs text-gray-400 leading-relaxed">
              势函数文件受原作者知识产权保护。请确认您有权在此平台分发该势函数。
            </p>

            <div className="space-y-3">
              {licenseOptions.map(opt => (
                <label key={opt.value} className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                  licenseType === opt.value ? 'bg-blue-900/30 border border-blue-500/50' : 'bg-gray-800/50 border border-gray-700 hover:border-gray-600'
                }`}>
                  <input type="radio" name="licenseType" value={opt.value} checked={licenseType === opt.value} onChange={() => setLicenseType(opt.value)} className="mt-1 accent-blue-500" />
                  <div>
                    <div className="text-sm font-medium text-white">{opt.label}</div>
                    <div className="text-xs text-gray-400 mt-0.5">{opt.desc}</div>
                  </div>
                </label>
              ))}
            </div>

            {licenseType === 'author_permission' && (
              <div className="mt-4 space-y-3">
                <div>
                  <label className={labelClass}>📎 上传授权证明文件 <span className="text-red-400">*</span></label>
                  <div className="flex items-center gap-3">
                    <input ref={fileInputRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.doc,.docx"
                      onChange={e => setAuthFile(e.target.files?.[0] || null)}
                      className="text-sm text-gray-300 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-600 file:text-white hover:file:bg-blue-500 file:cursor-pointer" />
                    {authFileUploading && <span className="text-xs text-blue-400">上传中…</span>}
                  </div>
                  {authFile && <div className="text-xs text-green-400 mt-1">✓ 已选择: {authFile.name} ({(authFile.size / 1024).toFixed(1)} KB)</div>}
                </div>

                {/* Template download — right after proof upload */}
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <div className="text-sm text-gray-300 font-medium mb-2">下载授权书模板（已自动填入上方信息）</div>
                  <div className="flex items-center gap-3 flex-wrap">
                    <button type="button" onClick={() => openTemplate('zh', false)}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-yellow-600/20 border border-yellow-600/40 text-sm text-yellow-400 hover:text-yellow-300 hover:bg-yellow-600/30 transition">
                      📄 中文版
                    </button>
                    <button type="button" onClick={() => openTemplate('en', false)}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-yellow-600/20 border border-yellow-600/40 text-sm text-yellow-400 hover:text-yellow-300 hover:bg-yellow-600/30 transition">
                      📄 English
                    </button>
                    <button type="button" onClick={() => openTemplate('zh', true)}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-600/40 text-sm text-blue-400 hover:text-blue-300 hover:bg-blue-600/30 transition">
                      🖨️ 打印/保存 PDF（中文）
                    </button>
                    <button type="button" onClick={() => openTemplate('en', true)}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-600/40 text-sm text-blue-400 hover:text-blue-300 hover:bg-blue-600/30 transition">
                      🖨️ Print/PDF (English)
                    </button>
                  </div>
                  <p className="text-xs text-gray-500 mt-2">
                    信息已从表单自动填入 → 打印 → 签字 → 扫描上传即可。也可先到<a href="/profile" className="text-blue-400 hover:underline" target="_blank">个人主页</a>填写完整个人信息。
                  </p>
                </div>

                <p className="text-xs text-gray-500">
                  支持 PDF、PNG、JPG、DOC 格式。请上传作者签署的授权书、邮件授权截图等。
                </p>
              </div>
            )}

            {licenseType === 'open_license' && (
              <div className="mt-4">
                <label className={labelClass}>许可证名称 <span className="text-red-400">*</span></label>
                <input type="text" value={licenseDetail} onChange={e => setLicenseDetail(e.target.value)} placeholder="e.g. CC-BY-4.0, MIT, GPL-3.0" className={inputClass} />
              </div>
            )}
          </div>

          {/* ========== 势函数文件 ========== */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">
              势函数文件（可选）
            </h2>
            <p className="text-xs text-gray-400">
              上传势函数源文件（如 .eam.alloy、.setfl、.meam 等）。如暂无文件可稍后补充。
            </p>
            <input type="file" accept=".eam.alloy,.eam.fs,.setfl,.eam,.meam,.param,.table,.txt,.json,.zip,.tar.gz,.gz"
              onChange={e => setPotentialFile(e.target.files?.[0] || null)}
              className="text-sm text-gray-300 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-600 file:text-white hover:file:bg-blue-500 file:cursor-pointer w-full" />
            {potentialFile && <div className="text-xs text-green-400 mt-2">✓ 已选择: {potentialFile.name} ({(potentialFile.size / 1024).toFixed(1)} KB)</div>}
          </div>

          {/* ========== 适用条件 ========== */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">适用条件（可选）</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>温度范围</label>
                <input type="text" value={tempRange} onChange={e => setTempRange(e.target.value)} placeholder="e.g. 300-1200 K" className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>相（逗号分隔）</label>
                <input type="text" value={phases} onChange={e => setPhases(e.target.value)} placeholder="e.g. fcc, bcc, liquid" className={inputClass} />
              </div>
            </div>
          </div>

          {/* ========== LAMMPS 配置 ========== */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">LAMMPS 配置（可选）</h2>
            <div>
              <label className={labelClass}>pair_style</label>
              <input type="text" value={pairStyle} onChange={e => setPairStyle(e.target.value)} placeholder="e.g. eam/fs" className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>pair_coeff</label>
              <input type="text" value={pairCoeff} onChange={e => setPairCoeff(e.target.value)} placeholder="e.g. * * Fe.eam.fs Fe" className={inputClass} />
            </div>
          </div>

          {/* ========== 元数据 ========== */}
          <div className={sectionClass}>
            <h2 className="text-base font-semibold text-white border-b border-gray-700 pb-2">元数据（可选）</h2>
            <div>
              <label className={labelClass}>标签（逗号分隔）</label>
              <input type="text" value={tags} onChange={e => setTags(e.target.value)} placeholder="e.g. radiation, defect, diffusion" className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>参考文献 DOI（逗号分隔）</label>
              <input type="text" value={doiRefs} onChange={e => setDoiRefs(e.target.value)} placeholder="e.g. 10.1103/PhysRevB.68.024102" className={inputClass} />
            </div>
          </div>

          {/* ========== 提交 ========== */}
          <div className="flex items-center justify-end gap-4 pt-2">
            <button type="button" onClick={() => router.back()} className="px-5 py-2 rounded-lg text-gray-300 hover:text-white hover:bg-gray-800 transition-colors">
              取消
            </button>
            <button type="submit" disabled={submitting || authFileUploading}
              className={'px-6 py-2 rounded-lg font-medium transition-colors ' +
                (submitting || authFileUploading ? 'bg-blue-800 text-blue-300 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-500 text-white')}>
              {submitting ? '提交中…' : '提交审核'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
