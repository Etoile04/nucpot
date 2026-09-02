"use client"

/**
 * `useDataLossNotice(dismissKey)` — per-row dismiss state hook.
 *
 * Spec §6.2 — when a user dismisses a row, the inline variant stays
 * visible (icon + "Show again" affordance) but the popover opens to a
 * "previously dismissed" state. State lives in `localStorage` under
 * `nfmd.dataloss.dismissed.{key}` with a 90-day TTL; logout clears the
 * entire `nfmd.dataloss.*` namespace.
 */

import { useCallback, useContext, useMemo } from "react"

import { DataLossNoticeContext } from "./DataLossNoticeProvider"

export interface UseDataLossNoticeResult {
  readonly isDismissed: boolean
  readonly dismiss: () => void
}

export function useDataLossNotice(
  dismissKey: string,
): UseDataLossNoticeResult {
  const ctx = useContext(DataLossNoticeContext)
  // Outside a provider (e.g. tests that don't mount the tree) the hook
  // reports not-dismissed so the component renders the full disclosure.
  const isDismissed = ctx ? ctx.isDismissed(dismissKey) : false
  const dismiss = useCallback((): void => {
    ctx?.dismiss(dismissKey)
  }, [ctx, dismissKey])

  return useMemo<UseDataLossNoticeResult>(
    () => ({ isDismissed, dismiss }),
    [isDismissed, dismiss],
  )
}