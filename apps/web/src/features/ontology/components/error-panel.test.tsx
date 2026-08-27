/**
 * Tests for ErrorPanel component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ErrorPanel } from './error-panel'

describe('ErrorPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // navigator.clipboard may not exist in jsdom
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  })

  it('renders error message', () => {
    render(<ErrorPanel variant='list' message='Something broke' />)
    expect(screen.getByText(/Something broke/)).toBeInTheDocument()
  })

  it('calls onRetry when retry button clicked', () => {
    const onRetry = vi.fn()
    render(<ErrorPanel variant='list' message='err' onRetry={onRetry} />)
    fireEvent.click(screen.getByText('Retry'))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('renders request ID copy button when requestId provided', () => {
    render(<ErrorPanel variant='list' message='err' requestId='abc-123' />)
    expect(screen.getByText('Copy request ID')).toBeInTheDocument()
  })

  it('copies request ID on click', async () => {
    render(<ErrorPanel variant='list' message='err' requestId='abc-123' />)
    fireEvent.click(screen.getByText('Copy request ID'))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('abc-123')
  })

  it('omits retry button when onRetry not provided', () => {
    render(<ErrorPanel variant='detail' message='fail' />)
    expect(screen.queryByText('Retry')).not.toBeInTheDocument()
  })

  it('has role=alert and aria-live=assertive', () => {
    render(<ErrorPanel variant='list' message='err' />)
    const el = screen.getByRole('alert')
    expect(el).toHaveAttribute('aria-live', 'assertive')
  })

  it('uses list variant labels by default', () => {
    render(<ErrorPanel />)
    expect(screen.getByText(/Couldn't load the ontology list/)).toBeInTheDocument()
  })

  it('includes HTTP status when provided', () => {
    render(<ErrorPanel variant='detail' httpStatus={503} />)
    expect(screen.getByText(/(503)/)).toBeInTheDocument()
  })
})
