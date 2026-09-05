/**
 * Resolve a potential's `file_url` into a URL the browser can actually fetch.
 *
 * Canonical form (NFM-4309 / BUG-37): every migrated row stores the backend
 * proxy download path `/api/v1/potentials/{id}/file`, which passes through
 * unchanged — nginx routes `/api/*` to the API in prod, so the browser
 * button and direct API consumers (AutoVC) share one anonymous URL.
 *
 * Historical `file_url` values still resolvable for unmigrated caches:
 *
 *   1. `https://…` — absolute URL (Supabase public objects). Passed through
 *      unchanged (NFM-4309: wrapping them in `/uploads/…` produced dead
 *      links for the 13 library rows).
 *
 *   2. `/storage/v1/object/public/…` — Supabase Storage path, MUST be
 *      prefixed with the Supabase project origin (NFM-3317).
 *
 *   3. `/uploads/<uuid>.<ext>` — upload-volume files, now served through
 *      the backend proxy by migration 083; kept for legacy caches.
 *
 *   4. bare `foo.eam.alloy` — legacy bare filename, assumed under `/uploads/`.
 *
 * `/app/uploads/...` (API-container-local paths) are dead links tracked in
 * BUG-37; they pass through unchanged and 404 visibly rather than silently
 * misresolving. Migration 083 eliminates them at the source.
 */

const SUPABASE_URL_FALLBACK = "https://gzhiqyopzlmnkdzammhx.supabase.co"

// Read lazily so tests (and non-Next runtimes) can observe env changes; in a
// Next.js build `process.env.NEXT_PUBLIC_*` is inlined at compile time.
function supabaseUrl(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? SUPABASE_URL_FALLBACK
}

export function resolveFileUrl(fileUrl: string): string {
  // Absolute URLs (Supabase public objects) work as-is.
  if (/^https?:\/\//i.test(fileUrl)) {
    return fileUrl
  }
  if (fileUrl.startsWith("/storage/v1/")) {
    return `${supabaseUrl()}${fileUrl}`
  }
  // Canonical proxy path (/api/v1/potentials/{id}/file) and legacy
  // site-relative /uploads/ paths are already site-relative.
  return fileUrl.startsWith("/") ? fileUrl : `/uploads/${fileUrl}`
}
