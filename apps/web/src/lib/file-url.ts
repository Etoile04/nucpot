/**
 * file_url trust contract (NFM-4458).
 *
 * Production invariants (verified 2026-09-08):
 *
 *   - Every `potentials.file_url` returned by the BFF is canonical —
 *     either the proxy path ``/api/v1/potentials/{id}/file`` or empty
 *     (the historical missing-file rows, BUG-06). The FastAPI
 *     `potential_file_resolver` and migration 083 already collapsed
 *     the four legacy forms (absolute Supabase URLs, Supabase-relative
 *     paths, ``/uploads/<key>``, bare filenames) and the
 *     ``/app/uploads/<file>`` dead-link form at the source.
 *
 * What this module does NOT do anymore:
 *
 *   - prepend the Supabase project origin (the legacy NFM-3317 fallback);
 *   - rewrite ``/storage/v1/`` to absolute Supabase URLs;
 *   - prefix bare filenames with ``/uploads/``;
 *   - accept ``/app/uploads/`` as a resolvable path.
 *
 * The frontend's job is now: emit the canonical URL unchanged, or
 * surface the "file missing" state when the canonical URL is empty.
 * The single render path lives in `<FileLink>` (apps/web/src/components/
 * potential/FileLink.tsx). This module only exposes the trivial
 * pass-through plus the filename-derivation helper (which still needs
 * `extra.file_storage` to give the browser a real `download=` name).
 */

const STORAGE_V1_MARKER = "/storage/v1/object/public/"

function lastPathSegment(path: string): string {
  const cleaned = path.split("?")[0]?.split("#")[0] ?? path
  const segments = cleaned.split("/")
  return segments[segments.length - 1] ?? ""
}

/**
 * Trust the backend canonicalization: return the canonical proxy URL
 * unchanged, or the empty string when the row carries no file.
 *
 * Returns "" (not null) so callers can use the result directly as an
 * `<a href>` / `download=` payload without a separate null check.
 */
export function resolveFileUrl(fileUrl: string | null | undefined): string {
  if (!fileUrl) return ""
  return fileUrl
}

/**
 * Display filename for a potential file (NFM-4309 → NFM-4458).
 *
 * The canonical proxy URL ends in the literal segment "file", so the
 * real name must come from the storage reference: the uploads key or
 * the first supabase object path (bucket prefix and origin stripped).
 */
export function resolveFileName(fileUrl: string, extra?: Record<string, unknown> | null): string {
  const storage = extra?.file_storage
  if (storage !== null && typeof storage === "object") {
    const key = (storage as { key?: unknown }).key
    if (typeof key === "string") {
      const name = lastPathSegment(key)
      if (name) return name
    }
    const objects = (storage as { objects?: unknown }).objects
    if (Array.isArray(objects)) {
      const first = objects.find((o): o is string => typeof o === "string" && o.length > 0)
      if (first) {
        const markerAt = first.indexOf(STORAGE_V1_MARKER)
        const path = markerAt >= 0 ? first.slice(markerAt + STORAGE_V1_MARKER.length) : first
        const name = lastPathSegment(path)
        if (name) return name
      }
    }
  }
  return lastPathSegment(fileUrl) || fileUrl
}
