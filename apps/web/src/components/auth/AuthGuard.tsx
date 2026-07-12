'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Spin } from 'antd'
import { getToken, clearToken } from '@/lib/api-client'

interface AuthGuardProps {
  readonly children: React.ReactNode
}

type AuthState = 'loading' | 'authenticated' | 'unauthenticated'

/**
 * Wraps protected routes and redirects unauthenticated users to /admin/login.
 * Validates JWT from localStorage (blog_admin_token) against /api/v1/auth/me.
 */
export default function AuthGuard({ children }: AuthGuardProps) {
  const router = useRouter()
  const pathname = usePathname()
  const [state, setState] = useState<AuthState>('loading')

  const validateToken = useCallback(async () => {
    const token = getToken()
    if (!token) {
      setState('unauthenticated')
      return
    }

    try {
      const response = await fetch('/api/v1/auth/me', {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      })

      if (!response.ok) {
        clearToken()
        setState('unauthenticated')
        return
      }

      setState('authenticated')
    } catch {
      // Network error — don't clear token, keep user on page
      setState('authenticated')
    }
  }, [])

  useEffect(() => {
    validateToken()
  }, [validateToken])

  useEffect(() => {
    if (state === 'unauthenticated') {
      const redirect = encodeURIComponent(pathname)
      router.replace(`/admin/login?redirect=${redirect}`)
    }
  }, [state, pathname, router])

  if (state === 'loading') {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <Spin size="large" />
      </div>
    )
  }

  if (state === 'unauthenticated') {
    return null
  }

  return <>{children}</>
}
