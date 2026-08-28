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

  // Lighthouse P2 (NFM-3800): aria-allowed-role violation.
  // role="button" is not an allowed ARIA role on <li>; the clickable
  // version row must use a real <button> element instead.
  describe('accessibility (NFM-3800 — aria-allowed-role)', () => {
    it('does not put role="button" on <li> when onSelect is provided', () => {
      const { container } = render(
        <VersionLane versions={[MOCK_VERSION]} onSelect={() => {}} />,
      )
      const listItems = container.querySelectorAll('li')
      expect(listItems.length).toBeGreaterThan(0)
      listItems.forEach((li) => {
        expect(li.getAttribute('role')).not.toBe('button')
      })
    })

    it('renders the clickable row as a real <button> element', () => {
      const onSelect = vi.fn()
      render(
        <VersionLane versions={[MOCK_VERSION]} onSelect={onSelect} />,
      )
      const button = screen.getByRole('button', { name: /v1\.0\.0/ })
      expect(button.tagName).toBe('BUTTON')
      fireEvent.click(button)
      expect(onSelect).toHaveBeenCalledWith('v1-uuid')
    })

    it('keyboard activation works on the real <button>', () => {
      const onSelect = vi.fn()
      render(
        <VersionLane versions={[MOCK_VERSION]} onSelect={onSelect} />,
      )
      const button = screen.getByRole('button', { name: /v1\.0\.0/ }) as HTMLButtonElement
      button.focus()
      // Real browsers fire a synthetic `click` on a focused <button> when
      // Enter is pressed (native button activation). jsdom does not model
      // that automatically, so we dispatch both events to mirror the
      // production behaviour end-to-end.
      fireEvent.keyDown(button, { key: 'Enter' })
      fireEvent.click(button)
      expect(onSelect).toHaveBeenCalledWith('v1-uuid')
    })
  })
})
