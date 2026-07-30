import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  ProvenanceBadge,
  resolveProvenance,
  PROVENANCE_LABELS,
} from './ProvenanceBadge'

describe('resolveProvenance', () => {
  // --- Straight server-provided values ---

  it('resolves the server value "llm" to llm', () => {
    expect(resolveProvenance('llm')).toBe('llm')
  })

  it('resolves the server value "manual" to manual', () => {
    expect(resolveProvenance('manual')).toBe('manual')
  })

  it('resolves the server value "mineru" to mineru', () => {
    expect(resolveProvenance('mineru')).toBe('mineru')
  })

  it('is case-insensitive and trims surrounding whitespace', () => {
    expect(resolveProvenance('  MinerU  ')).toBe('mineru')
    expect(resolveProvenance('LLM')).toBe('llm')
  })

  // --- Manual wins (NFM-2237 requirement 4) ---

  it('resolves to manual when the server reports both llm and manual', () => {
    expect(resolveProvenance(['llm', 'manual'])).toBe('manual')
  })

  it('resolves to manual regardless of ordering', () => {
    expect(resolveProvenance(['manual', 'llm'])).toBe('manual')
  })

  it('resolves to manual when a mineru item was manually corrected', () => {
    expect(resolveProvenance(['mineru', 'manual'])).toBe('manual')
  })

  it('resolves to manual from a comma-joined server value', () => {
    expect(resolveProvenance('llm,manual')).toBe('manual')
  })

  it('prefers mineru over llm when both are reported and manual is absent', () => {
    expect(resolveProvenance(['llm', 'mineru'])).toBe('mineru')
  })

  // --- Unknown / absent: never guess ---

  it('resolves a missing field to unknown rather than guessing llm', () => {
    expect(resolveProvenance(undefined)).toBe('unknown')
  })

  it('resolves null to unknown', () => {
    expect(resolveProvenance(null)).toBe('unknown')
  })

  it('resolves an empty string to unknown', () => {
    expect(resolveProvenance('')).toBe('unknown')
  })

  it('resolves an unrecognized value to unknown', () => {
    expect(resolveProvenance('gpt-4o')).toBe('unknown')
  })

  it('resolves an empty array to unknown', () => {
    expect(resolveProvenance([])).toBe('unknown')
  })

  it('does not infer provenance from a numeric confidence value', () => {
    expect(resolveProvenance(0.95)).toBe('unknown')
  })
})

describe('PROVENANCE_LABELS', () => {
  it('uses the exact labels required by NFM-2237', () => {
    expect(PROVENANCE_LABELS.llm).toBe('LLM提取')
    expect(PROVENANCE_LABELS.manual).toBe('手动')
    expect(PROVENANCE_LABELS.mineru).toBe('MinerU图')
  })
})

describe('ProvenanceBadge', () => {
  // --- Visible label text (requirement 1) ---

  it('renders the visible label LLM提取 for llm', () => {
    render(<ProvenanceBadge provenance="llm" />)
    expect(screen.getByText('LLM提取')).toBeDefined()
  })

  it('renders the visible label 手动 for manual', () => {
    render(<ProvenanceBadge provenance="manual" />)
    expect(screen.getByText('手动')).toBeDefined()
  })

  it('renders the visible label MinerU图 for mineru', () => {
    render(<ProvenanceBadge provenance="mineru" />)
    expect(screen.getByText('MinerU图')).toBeDefined()
  })

  it('applies manual-wins precedence when given a raw multi-value source', () => {
    render(<ProvenanceBadge provenance={['llm', 'manual']} />)
    expect(screen.getByText('手动')).toBeDefined()
    expect(screen.queryByText('LLM提取')).toBeNull()
  })

  // --- Visually distinct from one another (requirement 2) ---

  it('gives each provenance a distinct background color', () => {
    const bgOf = (provenance: 'llm' | 'manual' | 'mineru') => {
      const { container } = render(<ProvenanceBadge provenance={provenance} />)
      return (container.querySelector('[role="status"]')?.className ?? '')
        .split(' ')
        .filter((cls) => cls.startsWith('bg-'))
        .join(' ')
    }

    const backgrounds = [bgOf('llm'), bgOf('manual'), bgOf('mineru')]
    expect(new Set(backgrounds).size).toBe(3)
  })

  it('gives each provenance a distinct glyph so color is not the only cue', () => {
    const glyphOf = (provenance: 'llm' | 'manual' | 'mineru') => {
      const { container } = render(<ProvenanceBadge provenance={provenance} />)
      return container.querySelector('[aria-hidden="true"]')?.textContent ?? ''
    }

    const glyphs = [glyphOf('llm'), glyphOf('manual'), glyphOf('mineru')]
    expect(glyphs.every((g) => g.length > 0)).toBe(true)
    expect(new Set(glyphs).size).toBe(3)
  })

  // --- Manual is the most authoritative (requirement 3) ---

  it('renders manual with stronger weight than llm', () => {
    const classOf = (provenance: 'llm' | 'manual') => {
      const { container } = render(<ProvenanceBadge provenance={provenance} />)
      return container.querySelector('[role="status"]')?.className ?? ''
    }

    expect(classOf('manual')).toContain('font-semibold')
    expect(classOf('llm')).not.toContain('font-semibold')
  })

  it('renders llm as provisional using a dashed border', () => {
    render(<ProvenanceBadge provenance="llm" />)
    expect(screen.getByRole('status').className).toContain('border-dashed')
  })

  it('renders manual with a solid border', () => {
    render(<ProvenanceBadge provenance="manual" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('border-solid')
    expect(badge.className).not.toContain('border-dashed')
  })

  // --- Screen-reader legibility (acceptance criterion) ---

  it('exposes the provenance to screen readers via aria-label, not color', () => {
    render(<ProvenanceBadge provenance="llm" />)
    expect(screen.getByRole('status')).toHaveAttribute(
      'aria-label',
      '数据来源: LLM提取',
    )
  })

  it('exposes the manual provenance via aria-label', () => {
    render(<ProvenanceBadge provenance="manual" />)
    expect(screen.getByRole('status')).toHaveAttribute(
      'aria-label',
      '数据来源: 手动',
    )
  })

  it('marks the decorative glyph aria-hidden', () => {
    const { container } = render(<ProvenanceBadge provenance="mineru" />)
    expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy()
  })

  it('has role="status"', () => {
    render(<ProvenanceBadge provenance="manual" />)
    expect(screen.getByRole('status')).toBeDefined()
  })

  // --- Unknown provenance surfaces the gap instead of hiding it ---

  it('renders an explicit unknown label when the server omits provenance', () => {
    render(<ProvenanceBadge provenance={undefined} />)
    expect(screen.getByText('来源未知')).toBeDefined()
  })

  it('gives the unknown state an explanatory title', () => {
    render(<ProvenanceBadge provenance={undefined} />)
    expect(screen.getByRole('status').getAttribute('title')).toContain('未提供')
  })

  // --- Size variants follow the ConfidenceBadge idiom ---

  it('applies sm size classes by default', () => {
    render(<ProvenanceBadge provenance="llm" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('px-2')
    expect(badge.className).toContain('text-xs')
  })

  it('applies md size classes when specified', () => {
    render(<ProvenanceBadge provenance="llm" size="md" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('px-2.5')
    expect(badge.className).toContain('text-sm')
  })

  it('merges additional className', () => {
    render(<ProvenanceBadge provenance="llm" className="ml-2" />)
    expect(screen.getByRole('status').className).toContain('ml-2')
  })

  it('includes the shared badge base classes', () => {
    render(<ProvenanceBadge provenance="llm" />)
    const badge = screen.getByRole('status')
    expect(badge.className).toContain('inline-flex')
    expect(badge.className).toContain('rounded-full')
  })
})
