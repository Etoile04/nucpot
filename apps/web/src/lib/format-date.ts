/**
 * Date/time display helpers.
 *
 * NFM-4308 ② — API timestamps arrive as ISO 8601 (e.g.
 * `2026-09-01T01:29:54.036093Z`); user-facing surfaces must render them
 * localized (`2026-09-01 01:29`) instead of leaking the raw wire format.
 */

/** Pad a number to two digits (9 → "09"). */
function pad(n: number): string {
  return String(n).padStart(2, "0")
}

/**
 * Render an ISO timestamp in the viewer's local timezone as
 * `YYYY-MM-DD HH:mm`. Returns `"-"` for nullish/unparseable input.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "-"
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Render an ISO timestamp (or plain date) as `YYYY-MM-DD` — the
 * date-only convention shared by the /datasets list and detail pages
 * (NFM-5321 D2: raw ISO 8601 wire format must not reach the UI).
 * Returns `"-"` for nullish/unparseable input.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "-"
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
