/**
 * Locale-adjacent formatting helpers for the platform i18n service
 * (NFM-4179). Moved verbatim from
 * `apps/web/src/components/data-loss-notice/messages.ts`.
 */

/**
 * Format a date string for inline labels. Returns the original string
 * verbatim when it doesn't look like an ISO timestamp.
 *
 * ISO-8601 → YYYY-MM-DD for the common case; richer formats are left
 * to the caller. Date-formatting libraries (dayjs) are already in the
 * web app's `package.json` for callers that want a richer render.
 */
export function formatCreatedAt(
  raw: string | undefined,
  fallback: string,
): string {
  if (!raw) return fallback
  const isoDate = raw.match(/^(\d{4}-\d{2}-\d{2})/)
  return isoDate?.[1] ?? raw
}
