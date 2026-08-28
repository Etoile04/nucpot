/**
 * Tests for EmptyState component.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState } from './empty-state'

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title='Nothing here' />)
    expect(screen.getByRole('status')).toHaveTextContent('Nothing here')
  })

  it('renders description when provided', () => {
    render(<EmptyState title='Empty' description='Try again later' />)
    expect(screen.getByRole('status')).toHaveTextContent('Try again later')
  })

  it('renders action when provided', () => {
    render(<EmptyState title='Empty' action={<button>Create</button>} />)
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument()
  })

  it('omits description when not provided', () => {
    const { container } = render(<EmptyState title='Empty' />)
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs).toHaveLength(1)
  })

  it('omits action when not provided', () => {
    render(<EmptyState title='Empty' />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('has role=status and aria-live=polite', () => {
    render(<EmptyState title='Empty' />)
    const el = screen.getByRole('status')
    expect(el).toHaveAttribute('aria-live', 'polite')
  })
})
