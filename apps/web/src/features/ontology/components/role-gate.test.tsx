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

  it('renders children when user has domain_expert role', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'domain_expert' } })
    render(<RoleGate allow={['domain_expert']}><span>Secret</span></RoleGate>)
    expect(screen.getByText('Secret')).toBeInTheDocument()
  })

  it('hides children in hide mode for unauthorized (editor)', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'editor' } })
    const { container } = render(
      <RoleGate allow={['admin', 'domain_expert']} mode='hide'><span>Secret</span></RoleGate>
    )
    expect(container.innerHTML).toBe('')
  })

  it('hides children in hide mode for unauthorized (reviewer)', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'reviewer' } })
    const { container } = render(
      <RoleGate allow={['admin', 'domain_expert']} mode='hide'><span>Secret</span></RoleGate>
    )
    expect(container.innerHTML).toBe('')
  })

  it('hides children in hide mode when user is null (not logged in)', () => {
    mockUseAuth.mockReturnValue({ user: null })
    const { container } = render(
      <RoleGate allow={['admin', 'domain_expert']} mode='hide'><span>Secret</span></RoleGate>
    )
    expect(container.innerHTML).toBe('')
  })

  it('disables children in disable mode for unauthorized', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'editor' } })
    render(<RoleGate allow={['admin', 'domain_expert']} mode='disable'><span>Secret</span></RoleGate>)
    expect(screen.getByText('Secret').parentElement).toHaveAttribute('aria-disabled', 'true')
  })

  it('renders children directly (no wrapper) when authorized', () => {
    mockUseAuth.mockReturnValue({ user: { blog_role: 'admin' } })
    const { container } = render(<RoleGate allow={['admin']}><span>Content</span></RoleGate>)
    // Authorized renders fragment — no wrapping span with aria-disabled
    expect(container.querySelector('span[aria-disabled]')).toBeNull()
  })
})
