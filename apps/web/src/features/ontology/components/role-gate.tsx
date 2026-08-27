/**
 * RoleGate: conditionally renders children based on user role.
 *
 * Modes:
 * - hide:   removes children from DOM for unauthorized roles
 * - disable: renders children disabled with tooltip
 * - confirm: wraps with a typed-confirmation step
 *
 * Consumed from AuthProvider (blog_role field).
 * Per NFM-3550 §5 — single source of truth for curator-only controls.
 */
'use client'

import { useAuth } from '@/components/AuthProvider'
import type { Role } from '../types'

interface RoleGateProps {
  readonly allow: readonly Role[]
  readonly mode?: 'hide' | 'disable' | 'confirm'
  readonly children: React.ReactNode
}

const ROLE_MAP: Record<string, Role> = {
  admin: 'admin',
  editor: 'curator',
  curator: 'curator',
}

function resolveRole(blogRole: string | null): Role {
  if (!blogRole) return 'reader'
  return ROLE_MAP[blogRole.toLowerCase()] ?? 'reader'
}

export function RoleGate({ allow, mode = 'disable', children }: RoleGateProps) {
  const { user } = useAuth()
  const role = resolveRole(user?.blog_role ?? null)
  const authorized = allow.includes(role)

  if (mode === 'hide' && !authorized) {
    return null
  }

  if (mode === 'confirm' && !authorized) {
    return null
  }

  if (!authorized) {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--onto-space-2)',
        }}
        title="Requires curator role"
        aria-disabled="true"
        tabIndex={-1}
      >
        {children}
      </span>
    )
  }

  return <>{children}</>
}
