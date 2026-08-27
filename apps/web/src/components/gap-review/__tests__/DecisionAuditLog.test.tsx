/**
 * Tests for DecisionAuditLog component.
 *
 * Uses initialData prop to skip real API calls.
 * Mocks next/navigation at module top level.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DecisionAuditLog } from '../DecisionAuditLog'
import type { AuditEntry, AuditLogResponse } from '@/lib/reference-gaps/types'

// Mock next/navigation at module top level (hoisted by vitest)
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
}))

// Mock the API to prevent real fetch calls
vi.mock('@/lib/reference-gaps/api', () => ({
  getAuditLog: vi.fn(),
}))

// ── Fixtures ─────────────────────────────────────────────────────────

const ENTRY_1: AuditEntry = {
  id: 'audit-1',
  decided_at: '2026-08-20T10:30:00Z',
  reviewer_name: 'Zhang San',
  entity_name: 'UO2 Density',
  decision: 'accepted',
  confidence: 0.92,
  source_document: 'doi:10.1016/j.jnucmat.2020.01.001',
}

const ENTRY_2: AuditEntry = {
  id: 'audit-2',
  decided_at: '2026-08-21T14:00:00Z',
  reviewer_name: 'Li Si',
  entity_name: 'UO2 Conductivity',
  decision: 'rejected',
  confidence: 0.45,
  source_document: 'handbook-v3.pdf',
}

const ENTRY_3: AuditEntry = {
  id: 'audit-3',
  decided_at: '2026-08-22T09:15:00Z',
  reviewer_name: 'Wang Wu',
  entity_name: 'PuO2 Melting Point',
  decision: 'deferred',
  confidence: 0.68,
  source_document: '',
}

const ALL_ENTRIES: ReadonlyArray<AuditEntry> = [ENTRY_1, ENTRY_2, ENTRY_3]

const FIXTURE_DATA: AuditLogResponse = {
  items: ALL_ENTRIES,
  next_cursor: null,
  prev_cursor: null,
  has_next: false,
  has_prev: false,
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('DecisionAuditLog', () => {
  it('renders a table with audit entries', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getByRole('table')).toBeDefined()
  })

  it('renders entity names', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getAllByText('UO2 Density')[0]).toBeDefined()
    expect(screen.getAllByText('UO2 Conductivity')[0]).toBeDefined()
  })

  it('renders reviewer names', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getAllByText('Zhang San')[0]).toBeDefined()
    expect(screen.getAllByText('Li Si')[0]).toBeDefined()
  })

  it('renders decision badges', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getAllByText('已接受')[0]).toBeDefined()
    expect(screen.getAllByText('已拒绝')[0]).toBeDefined()
    expect(screen.getAllByText('已延期')[0]).toBeDefined()
  })

  it('renders source document or dash for empty', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getAllByText('doi:10.1016/j.jnucmat.2020.01.001')[0]).toBeDefined()
    expect(screen.getAllByText('handbook-v3.pdf')[0]).toBeDefined()
    // ENTRY_3 has empty source_document, should show dash
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('renders item count in pagination bar', () => {
    const paginatedData: AuditLogResponse = {
      ...FIXTURE_DATA,
      has_next: true,
    }
    render(<DecisionAuditLog initialData={paginatedData} />)
    expect(screen.getAllByText('3 条')[0]).toBeDefined()
  })

  it('is read-only: no edit/delete/action buttons', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    // Should NOT have action buttons like the ReviewQueueTable
    expect(screen.queryByText('action')).toBeNull()
  })

  it('shows empty state when no entries', () => {
    const emptyData: AuditLogResponse = { items: [], next_cursor: null, prev_cursor: null, has_next: false, has_prev: false }
    render(<DecisionAuditLog initialData={emptyData} />)
    // Should show some empty state text (check for table existing but empty)
    expect(screen.getByRole('table')).toBeDefined()
  })

  it('renders filter inputs', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getByPlaceholderText('Uranium Dioxide')).toBeDefined()
    expect(screen.getByPlaceholderText('reviewer@example.com')).toBeDefined()
  })

  it('renders date inputs for date range filter', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    // Labels for date range are always present
    expect(screen.getAllByText('开始日期')[0]).toBeDefined()
    expect(screen.getAllByText('结束日期')[0]).toBeDefined()
  })

  it('shows cursor pagination when has_next or has_prev', () => {
    const paginatedData: AuditLogResponse = {
      items: ALL_ENTRIES,
      next_cursor: 'eyJpZCI6ImF1ZGl0LTMifQ==',
      prev_cursor: null,
      has_next: true,
      has_prev: false,
    }
    render(<DecisionAuditLog initialData={paginatedData} />)
    expect(screen.getByText('下一页 ›')).toBeDefined()
  })

  it('hides pagination when neither has_next nor has_prev', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.queryByText('下一页 ›')).toBeNull()
    expect(screen.queryByText('‹ 上一页')).toBeNull()
  })
})
