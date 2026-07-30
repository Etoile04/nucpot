/**
 * useFormDraft — persist in-flight form values across the re-auth flow.
 *
 * NFM-2251 §d: when a refresh-token failure interrupts the user mid-form,
 * the form payload is held in ``sessionStorage`` (30-min TTL) so the user
 * doesn't lose their input after re-authentication lands them back on the
 * same page.
 *
 * Why sessionStorage and not localStorage:
 *   - sessionStorage is per-tab. If the user re-auths in a new tab they
 *     get a clean slate; we don't want to leak drafts across tabs.
 *   - TTL is enforced by ts + a wall-clock check on read.
 *
 * Usage:
 *   const [draft, setDraft] = useFormDraft("new-post", { title: "", body: "" })
 *   // ...later, after a successful submit:
 *   clearFormDraft("new-post")
 *
 * The hook is intentionally framework-agnostic about the value shape
 * (``T`` is whatever you want to serialize). The contract is:
 *   - On first render, return either the persisted draft (if fresh)
 *     or ``initial``.
 *   - On every value change within the same formId, write the draft +
 *     a timestamp.
 *   - On a ``formId`` change at runtime, re-read storage for the new
 *     formId and skip the write — never write the previous form's value
 *     under the new form's key.
 *   - On unmount, drafts are KEPT — clearing is the page's
 *     responsibility (``clearFormDraft``) so navigating away then
 *     back preserves work.
 */

import { useEffect, useRef, useState } from "react"

/** 30 minutes per NFM-2251 §d. */
export const FORM_DRAFT_TTL_MS = 30 * 60 * 1000

const KEY_PREFIX = "nfm:formDraft:"

interface DraftEnvelope<T> {
  readonly v: T
  readonly ts: number
}

function storageKey(formId: string): string {
  return `${KEY_PREFIX}${formId}`
}

function readDraft<T>(formId: string, initial: T): T {
  if (typeof window === "undefined") return initial
  try {
    const raw = window.sessionStorage.getItem(storageKey(formId))
    if (raw === null) return initial
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      typeof (parsed as { ts?: unknown }).ts !== "number" ||
      !("v" in parsed)
    ) {
      // Malformed envelope — drop it so the next write replaces cleanly.
      window.sessionStorage.removeItem(storageKey(formId))
      return initial
    }
    const env = parsed as DraftEnvelope<T>
    if (Date.now() - env.ts > FORM_DRAFT_TTL_MS) {
      window.sessionStorage.removeItem(storageKey(formId))
      return initial
    }
    return env.v
  } catch {
    // sessionStorage may throw in privacy mode or on quota errors.
    return initial
  }
}

function writeDraft<T>(formId: string, value: T): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(
      storageKey(formId),
      JSON.stringify({ v: value, ts: Date.now() } satisfies DraftEnvelope<T>),
    )
  } catch {
    // Quota or privacy mode — silently drop. The form still works in-memory.
  }
}

export function useFormDraft<T>(
  formId: string,
  initial: T,
): readonly [T, (next: T) => void] {
  const [value, setValue] = useState<T>(() => readDraft(formId, initial))
  const isFirstRender = useRef(true)
  // Track the formId we last observed so a runtime formId change is
  // detected as a real event — not as a "value change under formId B"
  // (which would clobber B's persisted draft with A's value).
  const lastSeenFormId = useRef(formId)
  // Capture `initial` at mount only — callers commonly pass an inline
  // object literal (``{ title: "", body: "" }``), whose reference
  // changes every render. We don't want that to retrigger the effect.
  const initialRef = useRef(initial)

  useEffect(() => {
    // Skip the very first render — we already loaded from storage and
    // re-writing on mount would just churn the timestamp.
    if (isFirstRender.current) {
      isFirstRender.current = false
      lastSeenFormId.current = formId
      return
    }

    // formId changed at runtime: re-read storage for the new formId,
    // and DO NOT write the old form's value under the new form's key.
    // Without this guard the effect fires with [formId=B, value=A]
    // and overwrites B's persisted draft with A's value.
    if (lastSeenFormId.current !== formId) {
      lastSeenFormId.current = formId
      setValue(readDraft(formId, initialRef.current))
      return
    }

    writeDraft(formId, value)
  }, [formId, value])

  return [value, setValue] as const
}

/** Drop a persisted draft. Call after a successful submit. */
export function clearFormDraft(formId: string): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.removeItem(storageKey(formId))
  } catch {
    // ignore
  }
}