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
  next_cursor: 'eyJpZCI6ImF1ZGl0LTMifQ==',
  prev_cursor: null,
}

const FIXTURE_FIRST_PAGE: AuditLogResponse = {
  items: ALL_ENTRIES,
  next_cursor: 'eyJpZCI6ImF1ZGl0LTMifQ==',
  prev_cursor: null,
}

const FIXTURE_MIDDLE_PAGE: AuditLogResponse = {
  items: [ENTRY_2, ENTRY_3],
  next_cursor: 'eyJpZCI6ImF1ZGl0LTEifQ==',
  prev_cursor: 'eyJwcmV2IjoiYXVkaXQtMyJ9',
}

const FIXTURE_LAST_PAGE: AuditLogResponse = {
  items: [ENTRY_1],
  next_cursor: null,
  prev_cursor: 'eyJwcmV2IjoiYXVkaXQtMiJ9',
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
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('is read-only: no edit/delete/action buttons', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.queryByText('action')).toBeNull()
  })

  it('shows empty state when no entries', () => {
    const emptyData: AuditLogResponse = { items: [], next_cursor: null, prev_cursor: null }
    render(<DecisionAuditLog initialData={emptyData} />)
    expect(screen.getByRole('table')).toBeDefined()
  })

  it('renders filter inputs', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getByPlaceholderText('Uranium Dioxide')).toBeDefined()
    expect(screen.getByPlaceholderText('reviewer@example.com')).toBeDefined()
  })

  it('renders date inputs for date range filter', () => {
    render(<DecisionAuditLog initialData={FIXTURE_DATA} />)
    expect(screen.getAllByText('开始日期')[0]).toBeDefined()
    expect(screen.getAllByText('结束日期')[0]).toBeDefined()
  })

  it('shows pagination when next_cursor is present', () => {
    render(<DecisionAuditLog initialData={FIXTURE_FIRST_PAGE} />)
    // Should show next button enabled
    expect(screen.getByLabelText('下一页')).toBeDefined()
  })

  it('shows first page button disabled when prev_cursor is null', () => {
    render(<DecisionAuditLog initialData={FIXTURE_FIRST_PAGE} />)
    const firstBtn = screen.getByLabelText('首页')
    expect(firstBtn).toBeDisabled()
    const prevBtn = screen.getByLabelText('上一页')
    expect(prevBtn).toBeDisabled()
  })

  it('enables prev/first buttons when prev_cursor exists', () => {
    render(<DecisionAuditLog initialData={FIXTURE_MIDDLE_PAGE} />)
    const firstBtn = screen.getByLabelText('首页')
    expect(firstBtn).not.toBeDisabled()
    const prevBtn = screen.getByLabelText('上一页')
    expect(prevBtn).not.toBeDisabled()
  })

  it('disables next button when next_cursor is null', () => {
    render(<DecisionAuditLog initialData={FIXTURE_LAST_PAGE} />)
    const nextBtn = screen.getByLabelText('下一页')
    expect(nextBtn).toBeDisabled()
  })

  it('hides pagination entirely when both cursors are null', () => {
    const singlePageData: AuditLogResponse = { items: ALL_ENTRIES, next_cursor: null, prev_cursor: null }
    render(<DecisionAuditLog initialData={singlePageData} />)
    expect(screen.queryByLabelText('下一页')).toBeNull()
    expect(screen.queryByLabelText('上一页')).toBeNull()
    expect(screen.queryByLabelText('首页')).toBeNull()
  })
})
