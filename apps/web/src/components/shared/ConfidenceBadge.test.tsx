import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConfidenceBadge, getConfidenceLabel } from './ConfidenceBadge'

describe('ConfidenceBadge', () => {
  // --- Color / tier mapping ---

  it('renders high confidence (> 0.8) with emerald colors', () => {
    render(<ConfidenceBadge value={0.95} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('text-emerald-400')
    expect(badge.className).toContain('bg-emerald-900/50')
  })

  it('renders medium confidence (0.6–0.8) with amber colors', () => {
    render(<ConfidenceBadge value={0.7} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('text-amber-400')
    expect(badge.className).toContain('bg-amber-900/50')
  })

  it('renders low confidence (< 0.6) with red colors', () => {
    render(<ConfidenceBadge value={0.3} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('text-red-400')
    expect(badge.className).toContain('bg-red-900/50')
  })

  it('treats exact 0.8 as medium (not high)', () => {
    render(<ConfidenceBadge value={0.8} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('text-amber-400')
  })

  it('treats exact 0.6 as medium', () => {
    render(<ConfidenceBadge value={0.6} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('text-amber-400')
  })

  // --- ARIA attributes ---

  it('has role="status"', () => {
    render(<ConfidenceBadge value={0.5} />)
    expect(screen.getByRole('status')).toBeDefined()
  })

  it('has correct aria-label with value and label', () => {
    render(<ConfidenceBadge value={0.5} />)
    expect(screen.getByRole('status')).toHaveAttribute(
      'aria-label',
      '置信度: 0.50, 低',
    )
  })

  it('has tooltip title with formatted value', () => {
    render(<ConfidenceBadge value={0.92} />)
    expect(screen.getByRole('status')).toHaveAttribute(
      'title',
      '0.92 — 高置信度',
    )
  })

  // --- Dot indicator ---

  it('renders a dot indicator with aria-hidden', () => {
    const { container } = render(<ConfidenceBadge value={0.9} />)
    const dot = container.querySelector('[aria-hidden="true"]')
    expect(dot).toBeTruthy()
    expect(dot?.className).toContain('rounded-full')
  })

  // --- Size variants ---

  it('applies sm size classes by default', () => {
    render(<ConfidenceBadge value={0.5} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('px-2')
    expect(badge.className).toContain('text-xs')
  })

  it('applies md size classes when specified', () => {
    render(<ConfidenceBadge value={0.5} size="md" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('px-2.5')
    expect(badge.className).toContain('text-sm')
  })

  // --- Label ---

  it('does not show label by default', () => {
    const { container } = render(<ConfidenceBadge value={0.9} />)
    expect(container.textContent).not.toContain('高')
  })

  it('shows label when showLabel is true', () => {
    render(<ConfidenceBadge value={0.9} showLabel />)
    expect(screen.getByText('高')).toBeDefined()
  })

  it('shows 中 label for medium confidence', () => {
    render(<ConfidenceBadge value={0.7} showLabel />)
    expect(screen.getByText('中')).toBeDefined()
  })

  it('shows 低 label for low confidence', () => {
    render(<ConfidenceBadge value={0.3} showLabel />)
    expect(screen.getByText('低')).toBeDefined()
  })

  // --- Additional className ---

  it('merges additional className', () => {
    render(<ConfidenceBadge value={0.5} className="ml-2" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('ml-2')
  })

  // --- Base classes ---

  it('includes base classes', () => {
    render(<ConfidenceBadge value={0.5} />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('inline-flex')
    expect(badge.className).toContain('rounded-full')
    expect(badge.className).toContain('font-mono')
    expect(badge.className).toContain('font-medium')
  })
})

describe('getConfidenceLabel', () => {
  it('returns 高 for > 0.8', () => {
    expect(getConfidenceLabel(0.81)).toBe('高')
    expect(getConfidenceLabel(1.0)).toBe('高')
  })

  it('returns 中 for 0.6–0.8', () => {
    expect(getConfidenceLabel(0.6)).toBe('中')
    expect(getConfidenceLabel(0.8)).toBe('中')
    expect(getConfidenceLabel(0.7)).toBe('中')
  })

  it('returns 低 for < 0.6', () => {
    expect(getConfidenceLabel(0.0)).toBe('低')
    expect(getConfidenceLabel(0.59)).toBe('低')
  })
})
