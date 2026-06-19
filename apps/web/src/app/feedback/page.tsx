'use client'

import { useState, FormEvent } from 'react'
import { useAuth } from '@/components/AuthProvider'

const TYPES = [
  { value: 'bug', label: 'Bug 报告' },
  { value: 'feature', label: '功能建议' },
  { value: 'data', label: '数据纠错' },
  { value: 'other', label: '其他' },
]

export default function FeedbackPage() {
  const { user } = useAuth()
  const [form, setForm] = useState({
    type: 'bug',
    title: '',
    description: '',
    email: user?.email || '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    if (!form.title.trim()) {
      setMsg({ type: 'err', text: '请填写标题' })
      return
    }
    setSubmitting(true)
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const data = await res.json()
      if (res.ok) {
        setMsg({ type: 'ok', text: '感谢您的反馈！' })
        setForm({ type: 'bug', title: '', description: '', email: user?.email || '' })
      } else {
        setMsg({ type: 'err', text: data.error || '提交失败' })
      }
    } catch {
      setMsg({ type: 'err', text: '网络错误' })
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 ' +
    'placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors'
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1'

  return (
    <div className="min-h-screen bg-gray-950 py-8 px-4">
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-white">意见反馈</h1>
          <p className="text-gray-400 text-sm mt-1">帮助我们改进 NucPot，欢迎提交任何反馈</p>
        </div>

        {msg && (
          <div className={`rounded-lg px-4 py-2 text-sm ${
            msg.type === 'ok'
              ? 'bg-green-900/40 border border-green-700 text-green-300'
              : 'bg-red-900/40 border border-red-700 text-red-300'
          }`}>
            {msg.type === 'ok' ? '✓' : '✗'} {msg.text}
          </div>
        )}

        <div className="bg-gray-900 rounded-xl border border-gray-800">
          <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
            <div>
              <label className={labelClass}>反馈类型</label>
              <select
                value={form.type}
                onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
                className={inputClass}
              >
                {TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>标题</label>
              <input
                type="text"
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                placeholder="简要描述您的反馈"
                className={inputClass}
                required
              />
            </div>
            <div>
              <label className={labelClass}>详细描述</label>
              <textarea
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="请提供更多细节…"
                rows={5}
                className={inputClass + ' resize-y'}
              />
            </div>
            <div>
              <label className={labelClass}>邮箱（可选）</label>
              <input
                type="email"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                placeholder="方便我们联系您"
                className={inputClass}
              />
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition disabled:opacity-50"
            >
              {submitting ? '提交中…' : '提交反馈'}
            </button>
          </form>
        </div>

        {/* GitHub Issues 引导 */}
        <div className="bg-gray-900/50 rounded-xl border border-gray-800 px-6 py-4 text-sm text-gray-400">
          <p className="mb-2">💡 您也可以直接在 GitHub 上提交 Issue：</p>
          <ul className="space-y-1 ml-4">
            <li>
              • 网站问题：
              <a
                href="https://github.com/Etoile04/nucpot/issues"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline ml-1"
              >
                https://github.com/Etoile04/nucpot/issues
              </a>
            </li>
            <li>
              • 自动验证功能：
              <a
                href="https://github.com/Etoile04/nucpot-autovc/issues"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline ml-1"
              >
                https://github.com/Etoile04/nucpot-autovc/issues
              </a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}
