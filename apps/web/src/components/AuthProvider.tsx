'use client'

import { createContext, useContext, useEffect, useState, useCallback } from 'react'

export interface AppUser {
  id: string
  username: string
  email: string
  full_name: string | null
  affiliation?: string | null
  title?: string | null
  phone?: string | null
  blog_role: string | null
  is_active: boolean
  created_at?: string
}

interface AuthContextType {
  user: AppUser | null
  loading: boolean
  signIn: (username: string, password: string) => Promise<{ error: string | null }>
  signUp: (email: string, password: string, username: string, fullName: string) => Promise<{ error: string | null }>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  user: null, loading: true,
  signIn: async () => ({ error: null }),
  signUp: async () => ({ error: null }),
  signOut: async () => {},
  refresh: async () => {},
})

export const useAuth = () => useContext(AuthContext)

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AppUser | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/auth/me', {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!res.ok) { setUser(null); return }
      const data = await res.json()
      setUser(data.data ?? data)
    } catch { setUser(null) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchProfile() }, [fetchProfile])

  async function signIn(username: string, password: string) {
    try {
      const body = new URLSearchParams()
      body.append('username', username)
      body.append('password', password)
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body, credentials: 'include',
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        return { error: err.detail ?? '登录失败' }
      }
      await fetchProfile()
      return { error: null }
    } catch { return { error: '网络错误' } }
  }

  async function signUp(email: string, password: string, username: string, fullName: string) {
    try {
      const res = await fetch('/api/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, username, full_name: fullName }),
        credentials: 'include',
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        return { error: err.detail ?? '注册失败' }
      }
      return signIn(username, password)
    } catch { return { error: '网络错误' } }
  }

  async function signOut() {
    try {
      await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'include' })
    } catch {}
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signUp, signOut, refresh: fetchProfile }}>
      {children}
    </AuthContext.Provider>
  )
}
