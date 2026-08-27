/**
 * RoleGate: conditionally renders children based on user role.
 *
 * Maps backend BlogRole values to ontology write permission.
 * Backend uses `domain_expert` for ontology write operations.
 * `admin` also has write access.
 *
 * Modes:
 * - hide:   removes children from DOM for unauthorized roles
 * - disable: renders children disabled with tooltip
 */
import { useAuth } from '@/components/AuthProvider'
import type { OntologyWriteRole } from '../types'

interface RoleGateProps {
  readonly allow: readonly OntologyWriteRole[]
  readonly mode?: 'hide' | 'disable'
  readonly children: React.ReactNode
}

/** Map backend BlogRole → ontology write permission. */
const ROLE_MAP: Record<string, OntologyWriteRole | null> = {
  admin: 'admin',
  domain_expert: 'domain_expert',
  editor: null,
  reviewer: null,
}

function resolveOntologyRole(blogRole: string | null): OntologyWriteRole | null {
  if (!blogRole) return null
  return ROLE_MAP[blogRole.toLowerCase()] ?? null
}

export function RoleGate({ allow, mode = 'disable', children }: RoleGateProps) {
  const { user } = useAuth()
  const role = resolveOntologyRole(user?.blog_role ?? null)
  const authorized = role !== null && allow.includes(role)

  if (mode === 'hide' && !authorized) {
    return null
  }

  if (!authorized) {
    return (
      <span
        className="inline-flex items-center gap-2"
        title="Requires domain_expert role"
        aria-disabled="true"
        tabIndex={-1}
      >
        {children}
      </span>
    )
  }

  return <>{children}</>
}
