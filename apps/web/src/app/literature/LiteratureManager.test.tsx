import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetailPanel } from './LiteratureManager'
import type { LiteratureDetail } from '@/lib/api-client'

/**
 * DetailPanel provenance labelling — NFM-2237.
 *
 * Each extraction result must carry exactly one source label, sourced from a
 * server-provided `provenance` field (never inferred client-side).
 */

function makeDetail(
  extractionResults: readonly Record<string, unknown>[],
): LiteratureDetail {
  return {
    id: '00000000-0000-0000-0000-0000000000ff',
    title: 'UO2 thermal properties',
    status: 'parsed',
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
    extraction_results: extractionResults,
  } as LiteratureDetail
}

const LLM_ITEM = {
  id: '00000000-0000-0000-0000-000000000001',
  item_type: 'property',
  property_name: 'thermal_conductivity',
  provenance: 'llm',
  confidence: 0.87,
}

const MANUAL_ITEM = {
  id: '00000000-0000-0000-0000-000000000002',
  item_type: 'property',
  property_name: 'melting_point',
  provenance: 'manual',
  confidence: 1.0,
}

const MINERU_ITEM = {
  id: '00000000-0000-0000-0000-000000000003',
  item_type: 'property',
  property_name: 'density_curve',
  provenance: 'mineru',
  confidence: 0.72,
}

describe('DetailPanel provenance labels', () => {
  it('labels an LLM-extracted item LLM提取', () => {
    render(<DetailPanel detail={makeDetail([LLM_ITEM])} />)
    expect(screen.getByLabelText('数据来源: LLM提取')).toBeDefined()
  })

  it('labels a manually entered item 手动', () => {
    render(<DetailPanel detail={makeDetail([MANUAL_ITEM])} />)
    expect(screen.getByLabelText('数据来源: 手动')).toBeDefined()
  })

  it('labels a MinerU figure item MinerU图', () => {
    render(<DetailPanel detail={makeDetail([MINERU_ITEM])} />)
    expect(screen.getByLabelText('数据来源: MinerU图')).toBeDefined()
  })

  it('renders exactly one provenance label per extraction item', () => {
    render(
      <DetailPanel detail={makeDetail([LLM_ITEM, MANUAL_ITEM, MINERU_ITEM])} />,
    )
    expect(screen.getAllByLabelText(/^数据来源: /)).toHaveLength(3)
  })

  it('labels an LLM-extracted then manually corrected item 手动, not LLM提取', () => {
    const corrected = {
      id: '00000000-0000-0000-0000-000000000004',
      item_type: 'property',
      property_name: 'heat_capacity',
      provenance: ['llm', 'manual'],
    }
    render(<DetailPanel detail={makeDetail([corrected])} />)

    expect(screen.getByLabelText('数据来源: 手动')).toBeDefined()
    expect(screen.queryByLabelText('数据来源: LLM提取')).toBeNull()
  })

  it('labels an item with no server provenance 来源未知 rather than guessing', () => {
    const noProvenance = {
      id: '00000000-0000-0000-0000-000000000005',
      item_type: 'property',
      property_name: 'grain_size',
      confidence: 0.91,
    }
    render(<DetailPanel detail={makeDetail([noProvenance])} />)

    expect(screen.getByLabelText('数据来源: 来源未知')).toBeDefined()
    expect(screen.queryByLabelText('数据来源: LLM提取')).toBeNull()
  })

  it('does not derive provenance from a high confidence score', () => {
    const highConfidenceNoProvenance = {
      id: '00000000-0000-0000-0000-000000000006',
      item_type: 'property',
      property_name: 'lattice_parameter',
      confidence: 1.0,
    }
    render(<DetailPanel detail={makeDetail([highConfidenceNoProvenance])} />)
    expect(screen.getByLabelText('数据来源: 来源未知')).toBeDefined()
  })

  it('does not derive provenance from review_status', () => {
    const corrected = {
      id: '00000000-0000-0000-0000-000000000007',
      item_type: 'property',
      property_name: 'porosity',
      review_status: 'corrected',
    }
    render(<DetailPanel detail={makeDetail([corrected])} />)
    expect(screen.getByLabelText('数据来源: 来源未知')).toBeDefined()
  })

  it('renders no provenance labels when there are no extraction results', () => {
    render(<DetailPanel detail={makeDetail([])} />)
    expect(screen.queryAllByLabelText(/^数据来源: /)).toHaveLength(0)
  })

  it('keeps rendering the existing property name alongside the new label', () => {
    render(<DetailPanel detail={makeDetail([LLM_ITEM])} />)
    expect(screen.getByText('thermal_conductivity')).toBeDefined()
    expect(screen.getByLabelText('数据来源: LLM提取')).toBeDefined()
  })
})
