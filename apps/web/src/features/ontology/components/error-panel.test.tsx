/**
 * Tests for error-panel component.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ErrorPanel } from './error-panel'

describe('ErrorPanel', () => {
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
})
