/** @jest-environment jsdom */
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { EntityMatchHighlight } from '../EntityMatchHighlight'
import type { TextSpan } from '@/lib/reference-gaps/types'

describe('EntityMatchHighlight', () => {
  const text = 'The phase transition of water occurs at 100 degrees.'

  it('renders plain text when no spans provided', () => {
    render(<EntityMatchHighlight text={text} matchSpans={[]} />)
    // No <mark> elements
    expect(screen.queryByRole('mark')).not.toBeInTheDocument()
    expect(screen.getByText(text)).toBeInTheDocument()
  })

  it('highlights a single span', () => {
    const spans: readonly TextSpan[] = [{ start: 4, end: 20 }]
    render(<EntityMatchHighlight text={text} matchSpans={spans} />)
    const mark = screen.getByRole('mark')
    expect(mark).toHaveTextContent('phase transition')
  })

  it('highlights multiple non-overlapping spans', () => {
    const spans: readonly TextSpan[] = [
      { start: 4, end: 20 },
      { start: 24, end: 29 },
    ]
    render(<EntityMatchHighlight text={text} matchSpans={spans} />)
    const marks = screen.getAllByRole('mark')
    expect(marks).toHaveLength(2)
    expect(marks[0]).toHaveTextContent('phase transition')
    expect(marks[1]).toHaveTextContent('water')
  })

  it('merges overlapping spans into one highlight', () => {
    const spans: readonly TextSpan[] = [
      { start: 4, end: 16 },
      { start: 10, end: 20 },
    ]
    render(<EntityMatchHighlight text={text} matchSpans={spans} />)
    const marks = screen.getAllByRole('mark')
    expect(marks).toHaveLength(1)
    expect(marks[0]).toHaveTextContent('phase transition')
  })

  it('handles out-of-bounds spans by clamping', () => {
    const spans: readonly TextSpan[] = [{ start: -5, end: 200 }]
    render(<EntityMatchHighlight text={text} matchSpans={spans} />)
    const mark = screen.getByRole('mark')
    expect(mark).toHaveTextContent(text)
  })

  it('handles empty text', () => {
    render(<EntityMatchHighlight text="" matchSpans={[{ start: 0, end: 5 }]} />)
    expect(screen.queryByRole('mark')).not.toBeInTheDocument()
  })

  it('passes through className', () => {
    render(
      <EntityMatchHighlight
        text={text}
        matchSpans={[]}
        className="text-sm"
      />,
    )
    const container = screen.getByText(text).closest('span.text-sm')
    expect(container).toBeInTheDocument()
  })
})
