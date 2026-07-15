'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Spin } from 'antd'

interface AuthGuardProps {
  readonly children: React.ReactNode
}

type AuthState = 'loading' | 'authenticated' | 'unauthenticated'

/**
 * Wraps protected routes and redirects unauthenticated users to /admin/login.
 * Validates session via HttpOnly cookie (credentials:"include").
 */
export default function AuthGuard({ children }: AuthGuardProps) {
  const router = useRouter()
  const pathname = usePathname()
  const [state, setState] = useState<AuthState>('loading')

  const validateSession = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/auth/me', {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })

      if (!response.ok) {
        setState('unauthenticated')
        return
      }

      setState('authenticated')
    } catch {
      // Network error — keep user on page
      setState('authenticated')
    }
  }, [])

  useEffect(() => {
    validateSession()
  }, [validateSession])

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
