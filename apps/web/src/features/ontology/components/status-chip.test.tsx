/**
 * Tests for StatusChip component.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusChip } from './status-chip'

describe('StatusChip', () => {
  it('renders draft status with correct label and role', () => {
    render(<StatusChip status='draft' />)
    const el = screen.getByRole('status')
    expect(el).toHaveTextContent('Draft')
    expect(el).toHaveAttribute('aria-label', 'Status: Draft')
  })

  it('renders published status', () => {
    render(<StatusChip status='published' />)
    expect(screen.getByRole('status')).toHaveTextContent('Published')
  })

  it('renders deprecated status', () => {
    render(<StatusChip status='deprecated' />)
    expect(screen.getByRole('status')).toHaveTextContent('Deprecated')
  })

  it('applies amber class for draft', () => {
    const { container } = render(<StatusChip status='draft' />)
    const el = container.firstElementChild as HTMLElement
    expect(el.className).toContain('text-amber')
  })

  it('applies emerald class for published', () => {
    const { container } = render(<StatusChip status='published' />)
    const el = container.firstElementChild as HTMLElement
    expect(el.className).toContain('text-emerald')
  })

  it('applies gray class for deprecated', () => {
    const { container } = render(<StatusChip status='deprecated' />)
    const el = container.firstElementChild as HTMLElement
    expect(el.className).toContain('text-gray')
  })
})
