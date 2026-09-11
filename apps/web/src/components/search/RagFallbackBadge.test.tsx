import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RagFallbackBadge } from './RagFallbackBadge'

// ---------------------------------------------------------------------------
// NFM-4734 §3 / AC-1 — reason-aware badge labels
// ---------------------------------------------------------------------------

describe('RagFallbackBadge', () => {
  it('renders nothing when fallback.used=false', () => {
    const { container } = render(
      <RagFallbackBadge
        fallback={{ used: false, kind: null, reason: 'none', originalError: null }}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('picks the semantic_timeout label', () => {
    render(
      <RagFallbackBadge
        fallback={{
          used: true,
          kind: 'iliKE',
          reason: 'semantic_timeout',
          originalError: null,
        }}
      />,
    )
    const badge = screen.getByTestId('rag-fallback-badge')
    expect(badge).toHaveTextContent('超时')
    expect(badge).toHaveAttribute('data-fallback-reason', 'semantic_timeout')
  })

  it('picks the semantic_empty label', () => {
    render(
      <RagFallbackBadge
        fallback={{
          used: true,
          kind: 'iliKE',
          reason: 'semantic_empty',
          originalError: null,
        }}
      />,
    )
    const badge = screen.getByTestId('rag-fallback-badge')
    expect(badge).toHaveTextContent('未命中')
    expect(badge).toHaveAttribute('data-fallback-reason', 'semantic_empty')
  })

  it('picks the provider_error label', () => {
    render(
      <RagFallbackBadge
        fallback={{
          used: true,
          kind: 'iliKE',
          reason: 'provider_error',
          originalError: 'db session unavailable',
        }}
      />,
    )
    const badge = screen.getByTestId('rag-fallback-badge')
    expect(badge).toHaveTextContent('异常')
    expect(badge).toHaveAttribute('data-fallback-reason', 'provider_error')
  })

  it('falls back to the legacy label when reason is missing', () => {
    render(
      <RagFallbackBadge
        fallback={{
          used: true,
          kind: 'iliKE',
          reason: 'none' as never, // legacy server: kind set, reason missing
          originalError: null,
        }}
      />,
    )
    const badge = screen.getByTestId('rag-fallback-badge')
    expect(badge).toBeInTheDocument()
  })

  it('surfaces originalError in the title attribute for ops correlation', () => {
    render(
      <RagFallbackBadge
        fallback={{
          used: true,
          kind: 'iliKE',
          reason: 'semantic_timeout',
          originalError: 'Read timed out after 10s',
        }}
      />,
    )
    const badge = screen.getByTestId('rag-fallback-badge')
    expect(badge).toHaveAttribute('title', 'Read timed out after 10s')
  })
})
