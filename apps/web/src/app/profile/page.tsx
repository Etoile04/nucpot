'use client'

import { useEffect, useState, FormEvent } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'
import { supabase } from '@/lib/supabase'

interface MyContribution {
  id: string
  name: string
  display_name: string
  type: string
  status: string // 'pending' | 'published' | 'rejected'
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
  const { user, profile, session, loading, signOut } = useAuth()
  const [contributions, setContributions] = useState<MyContribution[]>([])
  const [loadingContribs, setLoadingContribs] = useState(true)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [profileForm, setProfileForm] = useState<ProfileData>({
    full_name: '', affiliation: '', title: '', phone: '',
  })
  const [savedProfile, setSavedProfile] = useState<ProfileData | null>(null)
  const [profileMsg, setProfileMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Password change state
  const [pwdForm, setPwdForm] = useState({ current: '', newPwd: '', confirm: '' })
  const [pwdMsg, setPwdMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [pwdSaving, setPwdSaving] = useState(false)

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [loading, user, router])

  useEffect(() => {
    if (session) {
      fetchContributions()
      fetchProfileDetail()
    }
  }, [session])

  async function fetchContributions() {
    try {
      const res = await fetch('/api/auth/my-contributions', {
        headers: { Authorization: `Bearer ${session!.access_token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setContributions(data.contributions || [])
      }
    } catch {
      // Ignore
    } finally {
      setLoadingContribs(false)
    }
  }

  async function fetchProfileDetail() {
    try {
      const res = await fetch('/api/auth/profile', {
        headers: { Authorization: `Bearer ${session!.access_token}` },
      })
      if (res.ok) {
        const data = await res.json()
        const p = data.profile || {}
        const form: ProfileData = {
          full_name: p.full_name || '',
          affiliation: p.affiliation || '',
          title: p.title || '',
          phone: p.phone || '',
        }
        setProfileForm(form)
        setSavedProfile(form)
      }
    } catch {
      // Ignore
    }
  }

  async function handleProfileSave(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProfileMsg(null)
    try {
      const res = await fetch('/api/auth/profile', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session!.access_token}`,
        },
        body: JSON.stringify(profileForm),
      })
      const data = await res.json()
      if (res.ok) {
        setSavedProfile({ ...profileForm })
        setEditing(false)
        setProfileMsg({ type: 'ok', text: '个人信息已保存' })
      } else {
        setProfileMsg({ type: 'err', text: data.error || '保存失败' })
      }
    } catch {
      setProfileMsg({ type: 'err', text: '网络错误' })
    } finally {
      setSaving(false)
    }
  }

  async function handlePasswordChange(e: FormEvent) {
    e.preventDefault()
    setPwdMsg(null)
    if (pwdForm.newPwd !== pwdForm.confirm) {
      setPwdMsg({ type: 'err', text: '两次输入的密码不一致' })
      return
    }
    if (pwdForm.newPwd.length < 6) {
      setPwdMsg({ type: 'err', text: '新密码至少需要 6 位' })
      return
    }
    setPwdSaving(true)
    try {
      // 先验证当前密码
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: profile!.email || '',
        password: pwdForm.current,
      })
      if (signInError) {
        setPwdMsg({ type: 'err', text: '当前密码错误' })
        setPwdSaving(false)
        return
      }
      // 更新密码
      const { error } = await supabase.auth.updateUser({ password: pwdForm.newPwd })
      if (error) {
        setPwdMsg({ type: 'err', text: error.message })
      } else {
        setPwdMsg({ type: 'ok', text: '密码修改成功' })
        setPwdForm({ current: '', newPwd: '', confirm: '' })
      }
    } catch {
      setPwdMsg({ type: 'err', text: '网络错误' })
    } finally {
      setPwdSaving(false)
    }
  }

  function cancelEdit() {
    setProfileForm(savedProfile || { full_name: '', affiliation: '', title: '', phone: '' })
    setEditing(false)
    setProfileMsg(null)
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400">Loading...</div>
      </div>
    )
  }

  if (!user || !profile) return null

  const isAdmin = profile.role === 'admin'

  const statusBadge: Record<string, string> = {
    pending: 'bg-yellow-900/50 text-yellow-400 border-yellow-700',
    published: 'bg-green-900/50 text-green-400 border-green-700',
    rejected: 'bg-red-900/50 text-red-400 border-red-700',
  }

  const statusLabel: Record<string, string> = {
    pending: 'pending',
    published: '已发布',
    rejected: '已拒绝',
  }

  const inputClass =
    'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 ' +
    'placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors'
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1'

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Profile Card */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-blue-600 flex items-center justify-center text-white text-2xl font-bold uppercase">
              {profile.username[0]}
            </div>
            <div className="flex-1">
              <h1 className="text-xl font-bold text-white">{profile.username}</h1>
              <p className="text-gray-400 text-sm">{profile.email}</p>
              <div className="flex items-center gap-2 mt-1">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  isAdmin ? 'bg-yellow-900/50 text-yellow-300' : 'bg-blue-900/50 text-blue-300'
                }`}>
                  {isAdmin ? '管理员' : '贡献者'}
                </span>
                <span className="text-gray-500 text-xs">
                  注册于 {new Date(profile.created_at).toLocaleDateString('zh-CN')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Authorization Profile Info */}
        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-white">授权书个人信息</h2>
              <p className="text-xs text-gray-500 mt-0.5">用于自动填入势函数分发授权书</p>
            </div>
            {!editing && (
              <button
                onClick={() => setEditing(true)}
                className="px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-600/40 text-sm text-blue-400 hover:text-blue-300 hover:bg-blue-600/30 transition"
              >
                ✏️ 编辑
              </button>
            )}
          </div>

          {profileMsg && (
            <div className={`mx-6 mt-4 rounded-lg px-4 py-2 text-sm ${
              profileMsg.type === 'ok'
                ? 'bg-green-900/40 border border-green-700 text-green-300'
                : 'bg-red-900/40 border border-red-700 text-red-300'
            }`}>
              {profileMsg.type === 'ok' ? '✓' : '✗'} {profileMsg.text}
            </div>
          )}

          {editing ? (
            <form onSubmit={handleProfileSave} className="px-6 py-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className={labelClass}>姓名</label>
                  <input
                    type="text"
                    value={profileForm.full_name}
                    onChange={e => setProfileForm(f => ({ ...f, full_name: e.target.value }))}
                    placeholder="真实姓名"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass}>单位</label>
                  <input
                    type="text"
                    value={profileForm.affiliation}
                    onChange={e => setProfileForm(f => ({ ...f, affiliation: e.target.value }))}
                    placeholder="e.g. 湖南大学"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass}>职务/职称</label>
                  <input
                    type="text"
                    value={profileForm.title}
                    onChange={e => setProfileForm(f => ({ ...f, title: e.target.value }))}
                    placeholder="e.g. 教授"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass}>联系电话</label>
                  <input
                    type="text"
                    value={profileForm.phone}
                    onChange={e => setProfileForm(f => ({ ...f, phone: e.target.value }))}
                    placeholder="e.g. 138-xxxx-xxxx"
                    className={inputClass}
                  />
                </div>
              </div>
              <div className="flex items-center gap-3 pt-1">
                <button
                  type="submit"
                  disabled={saving}
                  className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition disabled:opacity-50"
                >
                  {saving ? '保存中…' : '保存'}
                </button>
                <button
                  type="button"
                  onClick={cancelEdit}
                  className="px-5 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
                >
                  取消
                </button>
              </div>
            </form>
          ) : (
            <div className="px-6 py-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">姓名</div>
                  <div className="text-sm text-gray-200">{savedProfile?.full_name || <span className="text-gray-600 italic">未填写</span>}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">单位</div>
                  <div className="text-sm text-gray-200">{savedProfile?.affiliation || <span className="text-gray-600 italic">未填写</span>}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">职务/职称</div>
                  <div className="text-sm text-gray-200">{savedProfile?.title || <span className="text-gray-600 italic">未填写</span>}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">联系电话</div>
                  <div className="text-sm text-gray-200">{savedProfile?.phone || <span className="text-gray-600 italic">未填写</span>}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 修改密码 */}
        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800">
            <h2 className="text-base font-semibold text-white">修改密码</h2>
          </div>
          {pwdMsg && (
            <div className={`mx-6 mt-4 rounded-lg px-4 py-2 text-sm ${
              pwdMsg.type === 'ok'
                ? 'bg-green-900/40 border border-green-700 text-green-300'
                : 'bg-red-900/40 border border-red-700 text-red-300'
            }`}>
              {pwdMsg.type === 'ok' ? '✓' : '✗'} {pwdMsg.text}
            </div>
          )}
          <form onSubmit={handlePasswordChange} className="px-6 py-4 space-y-4">
            <div>
              <label className={labelClass}>当前密码</label>
              <input
                type="password"
                value={pwdForm.current}
                onChange={e => setPwdForm(f => ({ ...f, current: e.target.value }))}
                placeholder="输入当前密码"
                className={inputClass}
                required
              />
            </div>
            <div>
              <label className={labelClass}>新密码</label>
              <input
                type="password"
                value={pwdForm.newPwd}
                onChange={e => setPwdForm(f => ({ ...f, newPwd: e.target.value }))}
                placeholder="至少 6 位"
                className={inputClass}
                required
              />
            </div>
            <div>
              <label className={labelClass}>确认新密码</label>
              <input
                type="password"
                value={pwdForm.confirm}
                onChange={e => setPwdForm(f => ({ ...f, confirm: e.target.value }))}
                placeholder="再次输入新密码"
                className={inputClass}
                required
              />
            </div>
            <button
              type="submit"
              disabled={pwdSaving}
              className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition disabled:opacity-50"
            >
              {pwdSaving ? '修改中…' : '修改密码'}
            </button>
          </form>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-blue-400">{contributions.length}</div>
            <div className="text-gray-500 text-xs mt-1">我的贡献</div>
          </div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-green-400">
              {contributions.filter(c => c.status === 'published').length}
            </div>
            <div className="text-gray-500 text-xs mt-1">已发布</div>
          </div>
          <div className="bg-gray-900 rounded-xl p-4 text-center border border-gray-800">
            <div className="text-2xl font-bold text-yellow-400">
              {contributions.filter(c => c.status === 'pending').length}
            </div>
            <div className="text-gray-500 text-xs mt-1">待审核</div>
          </div>
        </div>

        {/* Contributions */}
        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <div className="px-6 py-4 border-b border-gray-800">
            <h2 className="text-base font-semibold text-white">贡献记录</h2>
          </div>
          {loadingContribs ? (
            <div className="px-6 py-8 flex items-center justify-center gap-2 text-gray-500">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              加载中…
            </div>
          ) : contributions.length === 0 ? (
            <div className="px-6 py-8 text-center">
              <p className="text-gray-500 text-sm">暂无贡献记录，去<a href="/upload" className="text-blue-400 hover:underline">上传势函数</a> →</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
                    <th className="px-6 py-2 font-medium">名称</th>
                    <th className="px-6 py-2 font-medium">类型</th>
                    <th className="px-6 py-2 font-medium">状态</th>
                    <th className="px-6 py-2 font-medium">提交时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {contributions.map(c => (
                    <tr key={c.id} className="hover:bg-gray-800/30 transition-colors">
                      <td className="px-6 py-3">
                        <span className="text-sm text-gray-200 truncate block max-w-[200px]">
                          {c.display_name || c.name}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        <span className="text-sm text-gray-400">{c.type}</span>
                      </td>
                      <td className="px-6 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-xs border ${statusBadge[c.status] || 'bg-gray-800 text-gray-400'}`}>
                          {statusLabel[c.status] || c.status}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        <span className="text-xs text-gray-500">
                          {new Date(c.created_at).toLocaleString('zh-CN')}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={() => router.push('/upload')}
            className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition"
          >
            上传势函数
          </button>
          {isAdmin && (
            <button
              onClick={() => router.push('/admin')}
              className="px-5 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition"
            >
              管理后台
            </button>
          )}
          <button
            onClick={async () => { await signOut(); router.push('/') }}
            className="px-5 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
          >
            退出登录
          </button>
        </div>
      </div>
    </div>
  )
}
