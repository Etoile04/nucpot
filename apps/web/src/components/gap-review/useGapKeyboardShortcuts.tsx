"use client"

import { useCallback, useEffect, useRef, useState } from 'react'
import { Modal } from 'antd'

import type { GapCandidate, GapDecision } from '@/lib/gap-decisions/types'
import { GAP_REVIEW_SHORTCUTS } from '@/lib/gap-decisions/types'
import {
  buildDecisionPayload,
  filterByConfidence,
  submitBulkDecisions,
} from '@/lib/gap-decisions/bulk-decisions-api'

const INPUT_SELECTORS = 'input, textarea, [contenteditable="true"], select'

function isInputFocused(): boolean {
  const el = document.activeElement
  if (!el) return false
  return el.matches(INPUT_SELECTORS)
}

export interface UseGapKeyboardShortcutsOptions {
  readonly visibleItems: ReadonlyArray<GapCandidate>
  readonly selectedIds: ReadonlySet<string>
  readonly confidenceThreshold: number
  readonly isDrawerOpen: boolean
  readonly onAccept: (candidateIds: ReadonlyArray<string>) => void
  readonly onReject: (candidateIds: ReadonlyArray<string>) => void
  readonly onDefer: (candidateIds: ReadonlyArray<string>) => void
  readonly onCloseDrawer: () => void
  readonly onDeselectAll: () => void
  readonly onSuccess: () => void
  readonly onError: (message: string) => void
}

export function useGapKeyboardShortcuts({
  visibleItems,
  selectedIds,
  confidenceThreshold,
  isDrawerOpen,
  onAccept,
  onReject,
  onDefer,
  onCloseDrawer,
  onDeselectAll,
  onSuccess,
  onError,
}: UseGapKeyboardShortcutsOptions): {
  showShortcutsOverlay: boolean
  closeShortcutsOverlay: () => void
} {
  const [showOverlay, setShowOverlay] = useState(false)
  const loadingRef = useRef(false)

  const closeOverlay = useCallback(() => setShowOverlay(false), [])


  const executeAcceptAllVisible = useCallback(async () => {
    if (loadingRef.current) return
    const eligible = filterByConfidence(visibleItems, confidenceThreshold)
    if (eligible.length === 0) return

    return new Promise<void>((resolve) => {
      Modal.confirm({
        title: 'Confirm bulk accept',
        content: 'Accept ' + eligible.length + ' items (confidence >= ' + confidenceThreshold + ')',
        okText: 'Confirm',
        cancelText: 'Cancel',
        onOk: async () => {
          loadingRef.current = true
          try {
            const payload = buildDecisionPayload(eligible, 'accepted')
            await submitBulkDecisions(payload)
            onSuccess()
          } catch (err) {
            const msg = err instanceof Error ? err.message : 'Bulk operation failed'
            onError(msg)
          } finally {
            loadingRef.current = false
          }
          resolve()
        },
        onCancel: () => resolve(),
      })
    })
  }, [visibleItems, confidenceThreshold, onSuccess, onError])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (isInputFocused()) return
      if (e.ctrlKey || e.metaKey || e.altKey) return

      const key = e.key

      if (key === '?') {
        e.preventDefault()
        setShowOverlay((prev) => !prev)
        return
      }

      if (key === 'Escape') {
        e.preventDefault()
        if (isDrawerOpen) {
          onCloseDrawer()
        } else {
          onDeselectAll()
        }
        return
      }

      if (!isDrawerOpen) return

      if (key === 'A' && e.shiftKey) {
        e.preventDefault()
        executeAcceptAllVisible()
        return
      }

      const selectedArr = visibleItems
        .filter((c) => selectedIds.has(c.candidate_id))
        .map((c) => c.candidate_id)

      if (key === 'a' && !e.shiftKey) {
        e.preventDefault()
        onAccept(selectedArr)
        return
      }

      if (key === 'r') {
        e.preventDefault()
        onReject(selectedArr)
        return
      }

      if (key === 'd') {
        e.preventDefault()
        onDefer(selectedArr)
        return
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [
    visibleItems, selectedIds, isDrawerOpen,
    onAccept, onReject, onDefer,
    onCloseDrawer, onDeselectAll, executeAcceptAllVisible,
  ])

  return {
    showShortcutsOverlay: showOverlay,
    closeShortcutsOverlay: closeOverlay,
  }
}

export interface GapShortcutsOverlayProps {
  readonly visible: boolean
  readonly onClose: () => void
}

export function GapShortcutsOverlay({ visible, onClose }: GapShortcutsOverlayProps) {
  if (!visible) return null

  return (
    <div
      data-testid="shortcuts-overlay"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          onClose()
        }
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        role="dialog"
        aria-label="Keyboard shortcuts"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--color-bg-container, #fff)',
          borderRadius: 8,
          padding: '24px 32px',
          maxWidth: 420,
          width: '100%',
        }}
      >
        <h3 style={{ marginBottom: 16 }}>Keyboard Shortcuts</h3>
        <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {GAP_REVIEW_SHORTCUTS.map((s) => (
            <li
              key={s.key}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '6px 0',
                borderBottom: '1px solid var(--color-border-secondary, #f0f0f0)',
              }}
            >
              <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{s.label}</span>
              <span style={{ color: 'var(--color-text-secondary, #666)' }}>{s.description}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
