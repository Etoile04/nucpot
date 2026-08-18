/**
 * Resolve a potential's `file_url` into a URL the browser can actually fetch.
 *
 * Historical `file_url` values come in three shapes (NFM-3317):
 *
 *   1. `/storage/v1/object/public/potentials/...`
 *      Supabase Storage path — MUST be prefixed with the Supabase project
 *      origin. Serving it site-relative 404s (the site has no /storage/v1
 *      proxy), which is why every Huda-group download was dead even while
 *      the objects existed.
 *
 *   2. `/uploads/<uuid>.<ext>`
 *      Site-served file in `public/uploads/` — already correct as-is.
 *
 *   3. bare `foo.eam.alloy`
 *      Legacy bare filename — assumed to live under `/uploads/`.
 *
 * `/app/uploads/...` (API-container-local paths) are dead links tracked in
 * NFM-3317 follow-up; they pass through unchanged and will 404 visibly
 * rather than silently misresolving.
 */

const SUPABASE_URL_FALLBACK = "https://gzhiqyopzlmnkdzammhx.supabase.co"

// Read lazily so tests (and non-Next runtimes) can observe env changes; in a
// Next.js build `process.env.NEXT_PUBLIC_*` is inlined at compile time.
function supabaseUrl(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? SUPABASE_URL_FALLBACK
}

export function resolveFileUrl(fileUrl: string): string {
  if (fileUrl.startsWith("/storage/v1/")) {
    return `${supabaseUrl()}${fileUrl}`
  }
  // file_url is a relative path under /uploads/ (e.g., "/uploads/foo.eam.alloy")
  return fileUrl.startsWith("/") ? fileUrl : `/uploads/${fileUrl}`
}
