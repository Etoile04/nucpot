'use client'

import { useState, FormEvent } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useAuth } from '@/components/AuthProvider'

type Tab = 'login' | 'register'

export default function LoginPage() {
  const router = useRouter()
  const { signIn, signUp } = useAuth()
  const [tab, setTab] = useState<Tab>('login')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Login form state
  const [loginEmail, setLoginEmail] = useState('')
  const [loginPassword, setLoginPassword] = useState('')

  // Register form state
  const [regEmail, setRegEmail] = useState('')
  const [regPassword, setRegPassword] = useState('')
  const [regUsername, setRegUsername] = useState('')
  const [regFullName, setRegFullName] = useState('')

  async function handleLogin(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    const { error } = await signIn(loginEmail, loginPassword)
    setLoading(false)
    if (error) {
      setError(error)
    } else {
      router.push('/')
      router.refresh()
    }
  }

  async function handleRegister(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (!regUsername.trim()) {
      setError('用户名不能为空')
      return
    }
    if (regPassword.length < 6) {
      setError('密码至少需要 6 位')
      return
    }
    setLoading(true)
    const { error } = await signUp(regEmail, regPassword, regUsername.trim(), regFullName.trim())
    setLoading(false)
    if (error) {
      setError(error)
    } else {
      router.push('/')
      router.refresh()
    }
  }

  const inputClass =
    'w-full px-4 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition'

  return (
    <main className="flex flex-1 items-center justify-center min-h-[calc(100vh-73px)] px-4 py-12">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <Link href="/" className="text-2xl font-bold tracking-tight">
            NucPot <span className="text-blue-400 text-sm font-normal">核材料势函数库</span>
          </Link>
          <p className="text-gray-400 mt-2 text-sm">登录或注册以参与贡献</p>
        </div>

        <div className="bg-gray-800/60 border border-gray-700 rounded-2xl p-8 shadow-xl">
          {/* Tab switcher */}
          <div className="flex rounded-lg bg-gray-900 p-1 mb-6 gap-1">
            <button
              onClick={() => { setTab('login'); setError(null) }}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition ${
                tab === 'login'
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              登录
            </button>
            <button
              onClick={() => { setTab('register'); setError(null) }}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition ${
                tab === 'register'
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              注册
            </button>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-4 px-4 py-3 rounded-lg bg-red-900/40 border border-red-700 text-red-300 text-sm">
              {error}
            </div>
          )}

          {/* Login Form */}
          {tab === 'login' && (
            <form onSubmit={handleLogin} className="flex flex-col gap-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">邮箱</label>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="your@email.com"
                  className={inputClass}
                  value={loginEmail}
                  onChange={e => setLoginEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">密码</label>
                <input
                  type="password"
                  required
                  autoComplete="current-password"
                  placeholder="••••••••"
                  className={inputClass}
                  value={loginPassword}
                  onChange={e => setLoginPassword(e.target.value)}
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium transition mt-2"
              >
                {loading ? '登录中…' : '登录'}
              </button>
            </form>
          )}

          {/* Register Form */}
          {tab === 'register' && (
            <form onSubmit={handleRegister} className="flex flex-col gap-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">邮箱</label>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="your@email.com"
                  className={inputClass}
                  value={regEmail}
                  onChange={e => setRegEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">密码 <span className="text-gray-600">(至少 6 位)</span></label>
                <input
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder="••••••••"
                  className={inputClass}
                  value={regPassword}
                  onChange={e => setRegPassword(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">用户名</label>
                <input
                  type="text"
                  required
                  autoComplete="username"
                  placeholder="your_username"
                  className={inputClass}
                  value={regUsername}
                  onChange={e => setRegUsername(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">真实姓名 <span className="text-gray-600">(可选)</span></label>
                <input
                  type="text"
                  autoComplete="name"
                  placeholder="Zhang San"
                  className={inputClass}
                  value={regFullName}
                  onChange={e => setRegFullName(e.target.value)}
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium transition mt-2"
              >
                {loading ? '注册中…' : '创建账号'}
              </button>
            </form>
          )}
        </div>
      </div>
    </main>
  )
}
