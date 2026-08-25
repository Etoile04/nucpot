import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConfidenceMeter } from '../ConfidenceMeter'

describe('ConfidenceMeter', () => {
  it('renders the confidence value as percentage text', () => {
    render(<ConfidenceMeter value={0.75} />)
    expect(screen.getByText('75%')).toBeInTheDocument()
  })

  it('renders 0% for zero value', () => {
    render(<ConfidenceMeter value={0} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('renders 100% for value of 1', () => {
    render(<ConfidenceMeter value={1} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('applies danger color on inner bar for values below 0.4', () => {
    const { container } = render(<ConfidenceMeter value={0.3} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect(fill?.className).toContain('bg-red-500')
  })

  it('applies warning color on inner bar for values 0.4-0.7', () => {
    const { container } = render(<ConfidenceMeter value={0.5} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect(fill?.className).toContain('bg-amber-500')
  })

  it('applies success color on inner bar for values above 0.7', () => {
    const { container } = render(<ConfidenceMeter value={0.85} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect(fill?.className).toContain('bg-emerald-500')
  })

  it('sets correct aria-valuenow', () => {
    render(<ConfidenceMeter value={0.62} />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '0.62')
  })

  it('sets aria-valuemin to 0 and aria-valuemax to 1', () => {
    render(<ConfidenceMeter value={0.5} />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuemin', '0')
    expect(meter).toHaveAttribute('aria-valuemax', '1')
  })

  it('clamps values below 0 to 0%', () => {
    render(<ConfidenceMeter value={-0.5} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('clamps values above 1 to 100%', () => {
    render(<ConfidenceMeter value={1.5} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('sets fill width to the confidence percentage', () => {
    const { container } = render(<ConfidenceMeter value={0.6} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect((fill as HTMLElement).style.width).toBe('60%')
  })

  it('renders at boundary 0.4 as warning (inclusive)', () => {
    const { container } = render(<ConfidenceMeter value={0.4} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect(fill?.className).toContain('bg-amber-500')
  })

  it('renders at boundary 0.7 as warning (not >0.7)', () => {
    const { container } = render(<ConfidenceMeter value={0.7} />)
    const fill = container.querySelector('[role="meter"] > div')
    expect(fill?.className).toContain('bg-amber-500')
  })
})
