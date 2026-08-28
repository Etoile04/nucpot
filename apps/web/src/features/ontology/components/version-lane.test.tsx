/**
 * Tests for VersionLane component.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { VersionLane } from './version-lane'
import type { OntologyVersion } from '../types'

const MOCK_VERSION: OntologyVersion = {
  id: 'v1-uuid',
  version: '1.0.0',
  status: 'published',
  changelog: 'Initial version',
  created_by: 'admin',
  created_at: '2026-01-15T10:00:00Z',
  updated_at: '2026-01-15T10:00:00Z',
}

describe('VersionLane', () => {
  it('renders version number', () => {
    render(<VersionLane versions={[MOCK_VERSION]} />)
    expect(screen.getByText('v1.0.0')).toBeInTheDocument()
  })

  it('renders status label', () => {
    render(<VersionLane versions={[MOCK_VERSION]} />)
    expect(screen.getByText('Published')).toBeInTheDocument()
  })

  it('renders formatted date and author', () => {
    render(<VersionLane versions={[MOCK_VERSION]} />)
    expect(screen.getByText(/admin/)).toBeInTheDocument()
  })

  it('renders changelog', () => {
    render(<VersionLane versions={[MOCK_VERSION]} />)
    expect(screen.getByText('Initial version')).toBeInTheDocument()
  })

  it('omits changelog when null', () => {
    const noChangelog = { ...MOCK_VERSION, changelog: null }
    const { container } = render(<VersionLane versions={[noChangelog]} />)
    // No <p> with changelog text — only the date line
    const allP = container.querySelectorAll('p')
    expect(allP.length).toBe(0)
  })

  it('renders empty message when no versions', () => {
    render(<VersionLane versions={[]} />)
    expect(screen.getByText('No versions recorded.')).toBeInTheDocument()
  })

  it('calls onSelect when a version is clicked', () => {
    const onSelect = vi.fn()
    render(<VersionLane versions={[MOCK_VERSION]} onSelect={onSelect} />)
    fireEvent.click(screen.getByText('v1.0.0'))
    expect(onSelect).toHaveBeenCalledWith('v1-uuid')
  })

  it('marks selected version with aria-current', () => {
    render(<VersionLane versions={[MOCK_VERSION]} selectedId='v1-uuid' onSelect={() => {}} />)
    const item = screen.getByText('v1.0.0').closest('li')
    expect(item).toHaveAttribute('aria-current', 'true')
  })

  it('fades deprecated versions', () => {
    const deprecated = { ...MOCK_VERSION, status: 'deprecated' as const }
    const { container } = render(<VersionLane versions={[deprecated]} />)
    const li = container.querySelector('li')
    expect(li?.className).toContain('opacity-60')
  })

  it('has aria-label on the list', () => {
    render(<VersionLane versions={[MOCK_VERSION]} />)
    expect(screen.getByRole('list')).toHaveAttribute('aria-label', 'Version history')
  })
})
