/**
 * Tests for RoleGate component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RoleGate } from './role-gate'

const mockUseAuth = vi.fn()
vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}))

beforeEach(() => { vi.clearAllMocks() })

describe('RoleGate', () => {
  it('renders children when user has admin role', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'admin' } })
    render(<RoleGate allow={['admin']}><span>Secret</span></RoleGate>)
    expect(screen.getByText('Secret')).toBeInTheDocument()
  })

  it('renders children when user has editor role mapped to curator', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'editor' } })
    render(<RoleGate allow={['curator']}><span>Secret</span></RoleGate>)
    expect(screen.getByText('Secret')).toBeInTheDocument()
  })

  it('hides children in hide mode for unauthorized', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: null } })
    const { container } = render(
      <RoleGate allow={['curator']} mode='hide'><span>Secret</span></RoleGate>
    )
    expect(container.innerHTML).toBe('')
  })

  it('disables children in disable mode for unauthorized', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: null } })
    render(<RoleGate allow={['curator']} mode='disable'><span>Secret</span></RoleGate>)
    expect(screen.getByText('Secret').parentElement).toHaveAttribute('aria-disabled', 'true')
  })

  it('falls back to reader for unknown roles', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'viewer' } })
    const { container } = render(
      <RoleGate allow={['curator']} mode='hide'><span>Secret</span></RoleGate>
    )
    expect(container.innerHTML).toBe('')
  })
})
