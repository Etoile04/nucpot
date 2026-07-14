'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Spin } from 'antd'
import { getToken, authApi } from '@/lib/api-client'

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
      await authApi.getMe()
      setState('authenticated')
    } catch (error: unknown) {
      // Network error — don't clear token, keep user on page
      if (error instanceof TypeError) {
        setState('authenticated')
        return
      }
      // Auth error (401/403 etc.) — request() already clears token
      setState('unauthenticated')
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
