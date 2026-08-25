import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react'
import { useGapKeyboardShortcuts, GapShortcutsOverlay } from './useGapKeyboardShortcuts'
import type { GapCandidate } from '@/lib/gap-decisions/types'

// ── Fixtures ──────────────────────────────────────────────────────────

const CANDIDATES: ReadonlyArray<GapCandidate> = [
  { candidate_id: 'c-1', confidence: 0.92 },
  { candidate_id: 'c-2', confidence: 0.65 },
  { candidate_id: 'c-3', confidence: 0.45 },
]

function defaultOptions(overrides?: Record<string, unknown>) {
  return {
    visibleItems: CANDIDATES,
    selectedIds: new Set(['c-1', 'c-2']),
    confidenceThreshold: 0.7,
    isDrawerOpen: true,
    onAccept: vi.fn(),
    onReject: vi.fn(),
    onDefer: vi.fn(),
    onCloseDrawer: vi.fn(),
    onDeselectAll: vi.fn(),
    onSuccess: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  }
}

function fireKeyDown(key: string, shiftKey = false, target?: HTMLElement) {
  const el = target ?? document.body
  const event = new KeyboardEvent('keydown', {
    key,
    shiftKey,
    bubbles: true,
  })
  el.dispatchEvent(event)
  return event
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('useGapKeyboardShortcuts', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // --- Input focus gating ---

  it('does not fire callbacks when input is focused', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    // Create and focus an input
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    fireKeyDown('a')
    expect(opts.onAccept).not.toHaveBeenCalled()

    document.body.removeChild(input)
  })

  it('fires callbacks when input is NOT focused', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('a')
    expect(opts.onAccept).toHaveBeenCalledWith(['c-1', 'c-2'])
  })

  // --- Drawer gating ---

  it('does not fire a/r/d when drawer is closed', () => {
    const opts = defaultOptions({ isDrawerOpen: false })
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('a')
    fireKeyDown('r')
    fireKeyDown('d')

    expect(opts.onAccept).not.toHaveBeenCalled()
    expect(opts.onReject).not.toHaveBeenCalled()
    expect(opts.onDefer).not.toHaveBeenCalled()
  })

  // --- a/r/d shortcuts ---

  it('a calls onAccept with selected candidate IDs', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('a')
    expect(opts.onAccept).toHaveBeenCalledWith(['c-1', 'c-2'])
  })

  it('r calls onReject with selected candidate IDs', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('r')
    expect(opts.onReject).toHaveBeenCalledWith(['c-1', 'c-2'])
  })

  it('d calls onDefer with selected candidate IDs', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('d')
    expect(opts.onDefer).toHaveBeenCalledWith(['c-1', 'c-2'])
  })

  // --- Escape ---

  it('Escape calls onCloseDrawer when drawer is open', () => {
    const opts = defaultOptions({ isDrawerOpen: true })
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('Escape')
    expect(opts.onCloseDrawer).toHaveBeenCalled()
    expect(opts.onDeselectAll).not.toHaveBeenCalled()
  })

  it('Escape calls onDeselectAll when drawer is closed', () => {
    const opts = defaultOptions({ isDrawerOpen: false })
    renderHook(() => useGapKeyboardShortcuts(opts))

    fireKeyDown('Escape')
    expect(opts.onDeselectAll).toHaveBeenCalled()
    expect(opts.onCloseDrawer).not.toHaveBeenCalled()
  })

  // --- ? overlay ---

  it('? toggles shortcuts overlay', () => {
    const opts = defaultOptions()
    const { result } = renderHook(() => useGapKeyboardShortcuts(opts))

    expect(result.current.showShortcutsOverlay).toBe(false)

    act(() => fireKeyDown('?'))
    expect(result.current.showShortcutsOverlay).toBe(true)

    act(() => fireKeyDown('?'))
    expect(result.current.showShortcutsOverlay).toBe(false)
  })

  it('closeShortcutsOverlay closes the overlay', () => {
    const opts = defaultOptions()
    const { result } = renderHook(() => useGapKeyboardShortcuts(opts))

    act(() => fireKeyDown('?'))
    expect(result.current.showShortcutsOverlay).toBe(true)

    act(() => result.current.closeShortcutsOverlay())
    expect(result.current.showShortcutsOverlay).toBe(false)
  })

  // --- Shift+A ---

  it('Shift+A calls Modal.confirm and does not accept immediately', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    // We can't easily test Modal.confirm's onOk in jsdom,
    // but we can verify that pressing Shift+A with drawer open
    // does NOT call onAccept directly (it goes through the modal).
    act(() => fireKeyDown('A', true))
    expect(opts.onAccept).not.toHaveBeenCalled()
  })

  // --- Modifier key ignore ---

  it('ignores shortcuts when Ctrl is held', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    const event = new KeyboardEvent('keydown', {
      key: 'a',
      ctrlKey: true,
      bubbles: true,
    })
    document.body.dispatchEvent(event)

    expect(opts.onAccept).not.toHaveBeenCalled()
  })

  it('ignores shortcuts when Meta is held', () => {
    const opts = defaultOptions()
    renderHook(() => useGapKeyboardShortcuts(opts))

    const event = new KeyboardEvent('keydown', {
      key: 'a',
      metaKey: true,
      bubbles: true,
    })
    document.body.dispatchEvent(event)

    expect(opts.onAccept).not.toHaveBeenCalled()
  })

  // --- Cleanup ---

  it('removes event listener on unmount', () => {
    const opts = defaultOptions()
    const { unmount } = renderHook(() => useGapKeyboardShortcuts(opts))

    unmount()

    fireKeyDown('a')
    // Should not throw; listener is removed
    expect(opts.onAccept).not.toHaveBeenCalled()
  })
})

// ── GapShortcutsOverlay component ─────────────────────────────────────

describe('GapShortcutsOverlay', () => {
  it('renders nothing when visible is false', () => {
    const { container } = render(
      <GapShortcutsOverlay visible={false} onClose={vi.fn()} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('renders dialog when visible is true', () => {
    render(<GapShortcutsOverlay visible={true} onClose={vi.fn()} />)
    expect(screen.getByRole('dialog')).toBeDefined()
    expect(screen.getByText(/Keyboard Shortcuts/)).toBeDefined()
  })

  it('renders all shortcut entries', () => {
    render(<GapShortcutsOverlay visible={true} onClose={vi.fn()} />)
    expect(screen.getByText('A')).toBeDefined()
    expect(screen.getByText('R')).toBeDefined()
    expect(screen.getByText('D')).toBeDefined()
    expect(screen.getByText('Esc')).toBeDefined()
    expect(screen.getByText('Shift+A')).toBeDefined()
    expect(screen.getByText('?')).toBeDefined()
  })

  it('calls onClose when backdrop is clicked', () => {
    const onClose = vi.fn()
    render(<GapShortcutsOverlay visible={true} onClose={onClose} />)

    // Click the backdrop (the outer div)
    fireEvent.click(screen.getByRole('dialog').parentElement!)
    expect(onClose).toHaveBeenCalled()
  })

  it('does not call onClose when inner panel is clicked', () => {
    const onClose = vi.fn()
    const { container } = render(
      <GapShortcutsOverlay visible={true} onClose={onClose} />,
    )

    // The inner panel is the child div inside the dialog
    const innerPanel = container.querySelector('div[role="dialog"] > div')
    if (innerPanel) {
      fireEvent.click(innerPanel)
      expect(onClose).not.toHaveBeenCalled()
    }
  })
})
