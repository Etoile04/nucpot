'use client'

import { useCallback, useEffect, useState, FormEvent } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

interface MyContribution {
  id: string
  name: string
  display_name: string
  type: string
  status: string
  created_at: string
}

interface ProfileData {
  full_name: string
  affiliation: string
  title: string
  phone: string
}

export default function ProfilePage() {
  const router = useRouter()
  const { user, loading, signOut, refresh } = useAuth()
  const [contributions, setContributions] = useState<MyContribution[]>([])
  const [, setLoadingContribs] = useState(true)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [profileForm, setProfileForm] = useState<ProfileData>({
    full_name: '', affiliation: '', title: '', phone: '',
  })
  const [savedProfile, setSavedProfile] = useState<ProfileData | null>(null)
  const [profileMsg, setProfileMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const [pwdForm, setPwdForm] = useState({ current: '', newPwd: '', confirm: '' })
  const [pwdMsg, setPwdMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [pwdSaving, setPwdSaving] = useState(false)

  useEffect(() => {
    if (!loading && !user) router.push('/login')
  }, [loading, user, router])

  useEffect(() => {
    if (user) {
      const form: ProfileData = {
        full_name: user.full_name || '',
        affiliation: user.affiliation || '',
        title: user.title || '',
        phone: user.phone || '',
      }
      setProfileForm(form)
      setSavedProfile(form)
    }
  }, [user])

  const fetchContributions = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/contributions/me', { credentials: 'include' })
      if (res.ok) {
        const data = await res.json()
        setContributions(data.data?.contributions || data.contributions || [])
      }
    } catch {}
    finally { setLoadingContribs(false) }
  }, [])

  useEffect(() => { if (user) fetchContributions() }, [user, fetchContributions])

  async function handleProfileSave(e: FormEvent) {
    e.preventDefault()
    setSaving(true); setProfileMsg(null)
    try {
      const res = await fetch('/api/v1/auth/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profileForm),
        credentials: 'include',
      })
      if (res.ok) {
        setSavedProfile({ ...profileForm })
        setEditing(false)
        setProfileMsg({ type: 'ok', text: '个人信息已保存' })
        refresh()
      } else {
        const data = await res.json().catch(() => ({}))
        setProfileMsg({ type: 'err', text: data.detail || '保存失败' })
      }
    } catch { setProfileMsg({ type: 'err', text: '网络错误' }) }
    finally { setSaving(false) }
  }

  async function handlePasswordChange(e: FormEvent) {
    e.preventDefault(); setPwdMsg(null)
    if (pwdForm.newPwd !== pwdForm.confirm) { setPwdMsg({ type: 'err', text: '两次输入的密码不一致' }); return }
    if (pwdForm.newPwd.length < 8) { setPwdMsg({ type: 'err', text: '新密码至少需要 8 位，且包含字母和数字' }); return }
    setPwdSaving(true)
    try {
      const verifyBody = new URLSearchParams()
      verifyBody.append('username', user!.username)
      verifyBody.append('password', pwdForm.current)
      const verifyRes = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: verifyBody, credentials: 'include',
      })
      if (!verifyRes.ok) { setPwdMsg({ type: 'err', text: '当前密码错误' }); setPwdSaving(false); return }

      const res = await fetch('/api/v1/auth/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: pwdForm.newPwd }),
        credentials: 'include',
      })
      if (res.ok) {
        setPwdMsg({ type: 'ok', text: '密码修改成功' })
        setPwdForm({ current: '', newPwd: '', confirm: '' })
      } else {
        const data = await res.json().catch(() => ({}))
        setPwdMsg({ type: 'err', text: data.detail || '密码修改失败' })
      }
    } catch { setPwdMsg({ type: 'err', text: '网络错误' }) }
    finally { setPwdSaving(false) }
  }

  function cancelEdit() {
    setProfileForm(savedProfile || { full_name: '', affiliation: '', title: '', phone: '' })
    setEditing(false); setProfileMsg(null)
  }

  if (loading) return (<div className="min-h-screen bg-gray-950 flex items-center justify-center"><div className="text-gray-400">Loading...</div></div>)
  if (!user) return null

  const isAdmin = user.blog_role === 'admin'
  const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors'
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1'

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-blue-600 flex items-center justify-center text-white text-2xl font-bold uppercase">{user.username[0]}</div>
            <div className="flex-1">
              <h1 className="text-xl font-bold text-white">{user.username}</h1>
              <p className="text-gray-400 text-sm">{user.email}</p>
              <span className={`inline-block mt-1 px-2 py-0.5 rounded text-xs font-medium ${isAdmin ? 'bg-yellow-900/50 text-yellow-300' : 'bg-blue-900/50 text-blue-300'}`}>{isAdmin ? '管理员' : '贡献者'}</span>
            </div>
          </div>
        </div>

        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">个人信息</h2>
            {!editing && <button onClick={() => setEditing(true)} className="px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-600/40 text-sm text-blue-400 hover:text-blue-300 hover:bg-blue-600/30 transition">✏️ 编辑</button>}
          </div>
          {profileMsg && <div className={`mx-6 mt-4 rounded-lg px-4 py-2 text-sm ${profileMsg.type === 'ok' ? 'bg-green-900/40 border border-green-700 text-green-300' : 'bg-red-900/40 border border-red-700 text-red-300'}`}>{profileMsg.type === 'ok' ? '✓' : '✗'} {profileMsg.text}</div>}
          {editing ? (
            <form onSubmit={handleProfileSave} className="px-6 py-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div><label className={labelClass}>姓名</label><input type="text" value={profileForm.full_name} onChange={e => setProfileForm(f => ({ ...f, full_name: e.target.value }))} className={inputClass} /></div>
                <div><label className={labelClass}>单位</label><input type="text" value={profileForm.affiliation} onChange={e => setProfileForm(f => ({ ...f, affiliation: e.target.value }))} className={inputClass} /></div>
                <div><label className={labelClass}>职务/职称</label><input type="text" value={profileForm.title} onChange={e => setProfileForm(f => ({ ...f, title: e.target.value }))} className={inputClass} /></div>
                <div><label className={labelClass}>联系电话</label><input type="text" value={profileForm.phone} onChange={e => setProfileForm(f => ({ ...f, phone: e.target.value }))} className={inputClass} /></div>
              </div>
              <div className="flex items-center gap-3 pt-1">
                <button type="submit" disabled={saving} className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition disabled:opacity-50">{saving ? '保存中…' : '保存'}</button>
                <button type="button" onClick={cancelEdit} className="px-5 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition">取消</button>
              </div>
            </form>
          ) : (
            <div className="px-6 py-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div><div className="text-xs text-gray-500 mb-0.5">姓名</div><div className="text-sm text-gray-200">{savedProfile?.full_name || <span className="text-gray-600 italic">未填写</span>}</div></div>
                <div><div className="text-xs text-gray-500 mb-0.5">单位</div><div className="text-sm text-gray-200">{savedProfile?.affiliation || <span className="text-gray-600 italic">未填写</span>}</div></div>
                <div><div className="text-xs text-gray-500 mb-0.5">职务/职称</div><div className="text-sm text-gray-200">{savedProfile?.title || <span className="text-gray-600 italic">未填写</span>}</div></div>
                <div><div className="text-xs text-gray-500 mb-0.5">联系电话</div><div className="text-sm text-gray-200">{savedProfile?.phone || <span className="text-gray-600 italic">未填写</span>}</div></div>
              </div>
            </div>
          )}
        </div>

        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800"><h2 className="text-base font-semibold text-white">修改密码</h2></div>
          {pwdMsg && <div className={`mx-6 mt-4 rounded-lg px-4 py-2 text-sm ${pwdMsg.type === 'ok' ? 'bg-green-900/40 border border-green-700 text-green-300' : 'bg-red-900/40 border border-red-700 text-red-300'}`}>{pwdMsg.type === 'ok' ? '✓' : '✗'} {pwdMsg.text}</div>}
          <form onSubmit={handlePasswordChange} className="px-6 py-4 space-y-4">
            <div><label className={labelClass}>当前密码</label><input type="password" value={pwdForm.current} onChange={e => setPwdForm(f => ({ ...f, current: e.target.value }))} className={inputClass} required /></div>
            <div><label className={labelClass}>新密码</label><input type="password" value={pwdForm.newPwd} onChange={e => setPwdForm(f => ({ ...f, newPwd: e.target.value }))} placeholder="至少 8 位，含字母和数字" className={inputClass} required /></div>
            <div><label className={labelClass}>确认新密码</label><input type="password" value={pwdForm.confirm} onChange={e => setPwdForm(f => ({ ...f, confirm: e.target.value }))} className={inputClass} required /></div>
            <button type="submit" disabled={pwdSaving} className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition disabled:opacity-50">{pwdSaving ? '修改中…' : '修改密码'}</button>
          </form>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800"><div className="text-2xl font-bold text-blue-400">{contributions.length}</div><div className="text-gray-500 text-xs mt-1">我的贡献</div></div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800"><div className="text-2xl font-bold text-green-400">{contributions.filter(c => c.status === 'published').length}</div><div className="text-gray-500 text-xs mt-1">已发布</div></div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800"><div className="text-2xl font-bold text-yellow-400">{contributions.filter(c => c.status === 'pending').length}</div><div className="text-gray-500 text-xs mt-1">待审核</div></div>
        </div>

        <div className="flex gap-3">
          <button onClick={() => router.push('/upload')} className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition">上传势函数</button>
          {isAdmin && <button onClick={() => router.push('/admin')} className="px-5 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition">管理后台</button>}
          <button onClick={async () => { await signOut(); router.push('/') }} className="px-5 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition">退出登录</button>
        </div>
      </div>
    </div>
  )
}
