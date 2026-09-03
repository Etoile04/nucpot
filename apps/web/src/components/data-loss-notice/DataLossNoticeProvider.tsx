"use client"

/**
 * `<DataLossNoticeProvider>` — wires the global flag + per-row dismissal
 * state into the React tree.
 *
 * Spec §6.1 (flag) + §6.2 (dismiss). The provider is mounted at the
 * root layout next to `<SessionProvider>` so every page inherits the
 * same flag value and dismissal namespace.
 *
 * Flag resolution happens once on mount and is then cached for the
 * session — re-rendering the tree never re-reads the env (avoids
 * surprise behavior across hot reloads).
 */

import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type JSX,
  type ReactNode,
} from "react"

import {
  resolveFeatureFlag,
  setRuntimeOverride,
} from "./feature-flag"
import type { DataLossNoticeContextValue } from "./types"

const STORAGE_PREFIX = "nfmd.dataloss.dismissed."
const DISMISS_TTL_MS = 90 * 24 * 60 * 60 * 1000

interface DismissedRecord {
  readonly t: number
}

export const DataLossNoticeContext = createContext<DataLossNoticeContextValue | null>(null)

function safeReadStorage(): Record<string, DismissedRecord> {
  if (typeof window === "undefined") return {}
  const out: Record<string, DismissedRecord> = {}
  try {
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i)
      if (!key || !key.startsWith(STORAGE_PREFIX)) continue
      const raw = window.localStorage.getItem(key)
      if (!raw) continue
      try {
        const parsed = JSON.parse(raw) as unknown
        if (
          parsed &&
          typeof parsed === "object" &&
          "t" in parsed &&
          typeof (parsed as { t: unknown }).t === "number"
        ) {
          const k = key.slice(STORAGE_PREFIX.length)
          out[k] = parsed as DismissedRecord
        }
      } catch {
        // Skip malformed entries silently — the next write overwrites
        // them with a clean record.
      }
    }
  } catch {
    // localStorage may be disabled (private mode, quota) — degrade
    // gracefully to "no dismiss state".
  }
  return out
}

function safeWriteRecord(key: string, record: DismissedRecord): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(
      `${STORAGE_PREFIX}${key}`,
      JSON.stringify(record),
    )
  } catch {
    // Quota or disabled — best-effort, never throw.
  }
}

function safeRemoveKey(key: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(`${STORAGE_PREFIX}${key}`)
  } catch {
    // Best-effort.
  }
}

function pruneExpired(records: Record<string, DismissedRecord>): void {
  if (typeof window === "undefined") return
  const cutoff = Date.now() - DISMISS_TTL_MS
  for (const [key, value] of Object.entries(records)) {
    if (value.t < cutoff) safeRemoveKey(key)
  }
}

export interface DataLossNoticeProviderProps {
  /**
   * Override the flag at runtime. Used by the admin debug switch +
   * integration tests, and by `<DataLossNoticeGate>` (NFM-4180), which
   * feeds in the live flag-service value. Resolves precedence over the
   * flag-service cache.
   */
  readonly forceEnabled?: boolean | null
  readonly children: ReactNode
}

export function DataLossNoticeProvider({
  forceEnabled = null,
  children,
}: DataLossNoticeProviderProps): JSX.Element {
  // Sync the runtime override as a side-effect so consumers of
  // `resolveFeatureFlag()` (the component itself) see the provider's
  // value without prop-drilling.
  useEffect((): (() => void) => {
    setRuntimeOverride(forceEnabled)
    return (): void => {
      setRuntimeOverride(null)
    }
  }, [forceEnabled])

  // NFM-4180: `<DataLossNoticeGate>` feeds the live flag-service value
  // in as `forceEnabled`. Resolve it directly (instead of through the
  // module override, which the effect above only sets after this
  // render) so the context updates in the same render as the gate.
  const snapshot = useMemo(
    () =>
      forceEnabled !== null
        ? { enabled: forceEnabled, source: "provider" as const }
        : resolveFeatureFlag(),
    [forceEnabled],
  )
  const [records, setRecords] = useState<Record<string, DismissedRecord>>({})

  // Read + prune on mount.
  useEffect((): void => {
    const initial = safeReadStorage()
    pruneExpired(initial)
    setRecords(safeReadStorage())
  }, [])

  const isDismissed = useCallback(
    (key: string): boolean => {
      const record = records[key]
      if (!record) return false
      if (record.t < Date.now() - DISMISS_TTL_MS) return false
      return true
    },
    [records],
  )

  const dismiss = useCallback((key: string): void => {
    const record: DismissedRecord = { t: Date.now() }
    safeWriteRecord(key, record)
    setRecords((prev) => ({ ...prev, [key]: record }))
  }, [])

  const clearAllDismissed = useCallback((): void => {
    if (typeof window === "undefined") return
    const toRemove: string[] = []
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i)
      if (key && key.startsWith(STORAGE_PREFIX)) toRemove.push(key)
    }
    toRemove.forEach(safeRemoveKey)
    setRecords({})
  }, [])

  const value = useMemo<DataLossNoticeContextValue>(
    () => ({
      isEnabled: snapshot.enabled,
      isDismissed,
      dismiss,
      clearAllDismissed,
    }),
    [snapshot.enabled, isDismissed, dismiss, clearAllDismissed],
  )

  return (
    <DataLossNoticeContext.Provider value={value}>
      {children}
    </DataLossNoticeContext.Provider>
  )
}