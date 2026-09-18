/**
 * BUG-03 (NFM-4085): public blog data layer.
 *
 * Previously the public blog pages read `content/blog/*.md` from the
 * filesystem at build time (SSG). The admin publish workflow writes to
 * the *api* container's (non-persistent) content dir, so freshly
 * published posts never appeared — and vanished on container rebuild.
 *
 * Now the pages fetch published posts from the new public API
 * (`GET /api/v1/blog/public[/{slug}]`, DB as the single source of
 * truth) with ISR (60s revalidate) so publishes appear within a minute
 * without a rebuild. The markdown-file library stays for legacy seeds.
 */
import type { BlogPost, BlogPostMeta } from "./types"

interface PublicPostDto {
  readonly slug: string
  readonly title: string
  readonly summary: string | null
  readonly tags: string[] | null
  readonly author_name: string | null
  readonly published_at: string | null
  readonly created_at: string
  readonly content: string | null
}

function toMeta(p: PublicPostDto): BlogPostMeta {
  return {
    slug: p.slug,
    title: p.title,
    date: (p.published_at ?? p.created_at).slice(0, 10),
    summary: p.summary ?? "",
    tags: p.tags ?? [],
    author: p.author_name ?? "NucPot",
    status: "published",
  }
}

function toPost(p: PublicPostDto): BlogPost {
  return { slug: p.slug, frontmatter: toMeta(p), content: p.content ?? "" }
}

/**
 * During `next build` no API is reachable, and a relative-URL fetch against
 * nothing can stall past the 60s per-page build timeout (every /blog page
 * then fails after 3 retries). Serve the FS seeds at build time; ISR picks
 * up live DB content on the first runtime revalidation.
 */
function isProductionBuild(): boolean {
  return process.env.NEXT_PHASE === "phase-production-build"
}

/**
 * NFM-4940 residual: the public API returns its payload bare — the list
 * endpoint a JSON array, the detail endpoint a JSON object (FastAPI
 * `response_model`, see apps/api/src/nfm_db/api/v1/blog.py) — NOT the
 * `{ success, data }` envelope. The first fix read `body.data` off the
 * bare array (`undefined` → silently empty list) and off the bare object
 * (→ always-404 detail). Accept both shapes so neither contract can
 * blank the blog silently again.
 */
function isEnvelope(body: unknown): body is { data?: unknown } {
  return typeof body === "object" && body !== null && !Array.isArray(body) && "data" in body
}

function extractList(body: unknown): readonly PublicPostDto[] {
  if (isEnvelope(body)) {
    return Array.isArray(body.data) ? body.data : []
  }
  if (!Array.isArray(body)) {
    return []
  }
  return body.filter((p): p is PublicPostDto => typeof p === "object" && p !== null)
}

function extractPost(body: unknown): PublicPostDto | null {
  const payload = isEnvelope(body) ? body.data : body
  if (
    typeof payload === "object" &&
    payload !== null &&
    !Array.isArray(payload) &&
    typeof (payload as PublicPostDto).slug === "string"
  ) {
    return payload as PublicPostDto
  }
  return null
}

/**
 * Next.js hands dynamic-route params over still URL-encoded: for
 * `/blog/技术总结…` `params.slug` arrives as `%E6%8A%80…`. Callers pass
 * that raw value through, so decode once before it reaches either the
 * API path (encodeURIComponent on top would double-encode → guaranteed
 * 404) or the FS seed lookup (which indexes decoded slugs). Malformed
 * percent-sequences fall back to the raw value.
 *
 * NFM-4942: exported so pages can decode `params.slug` once at the
 * boundary — the prev/next lookup and the direct FS-seed fallback in
 * the detail page also need the decoded form.
 */
export function decodeSlug(slug: string): string {
  try {
    return decodeURIComponent(slug)
  } catch {
    return slug
  }
}

/** Upper bound for one public-blog API call (ISR revalidation included). */
const FETCH_TIMEOUT_MS = 10_000

/**
 * NFM-4940: this module runs in server components (SSR), where a relative
 * fetch path throws in Node (`Failed to parse URL from /api/v1/...`). The
 * old silent catch then fell back to the FS seeds — empty in prod — so no
 * admin-published post ever rendered publicly. Always resolve an absolute
 * base, mirroring kg-graph-api.ts: API_SERVER_URL first, then the
 * Docker-internal service DNS so SSR resolves inside any container.
 */
function apiBaseUrl(): string {
  return process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"
}

/** Shared fetch options for the public blog API (ISR + hard timeout). */
function publicFetchOptions(): RequestInit {
  return {
    headers: { Accept: "application/json" },
    next: { revalidate: 60 },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  } as RequestInit
}

/** Fetch published posts from the public API; fall back to FS seed posts. */
export async function getPublishedPosts(): Promise<readonly BlogPostMeta[]> {
  if (!isProductionBuild()) {
    try {
      const res = await fetch(`${apiBaseUrl()}/api/v1/blog/public`, publicFetchOptions())
      if (!res.ok) {
        console.error(
          `[blog] public posts fetch failed: ${res.status} ${res.statusText} — falling back to seed posts`,
        )
      } else {
        const body: unknown = await res.json()
        const items = extractList(body).map(toMeta)
        if (items.length > 0) return items
      }
    } catch (err) {
      // API unavailable → fall through to the FS seed posts below, but
      // never silently (NFM-4940 AC-4): the old quiet catch hid the
      // relative-URL failure for weeks.
      console.error("[blog] public posts fetch failed — falling back to seed posts:", err)
    }
  }
  // Legacy fallback: build-time markdown seeds (no dynamic import cycle).
  const { getAllPosts } = await import("./posts")
  return getAllPosts().filter((p) => p.status === "published")
}

/** Fetch one published post by slug (null when missing/unpublished). */
export async function getPublishedPost(slugInput: string): Promise<BlogPost | null> {
  const slug = decodeSlug(slugInput)
  if (!isProductionBuild()) {
    try {
      const res = await fetch(
        `${apiBaseUrl()}/api/v1/blog/public/${encodeURIComponent(slug)}`,
        publicFetchOptions(),
      )
      if (!res.ok) {
        console.error(
          `[blog] post fetch failed for "${slug}": ${res.status} ${res.statusText} — falling back to seed posts`,
        )
      } else {
        const body: unknown = await res.json()
        const post = extractPost(body)
        if (post) return toPost(post)
      }
    } catch (err) {
      // fall through to legacy seeds, logged (NFM-4940 AC-4)
      console.error(`[blog] post fetch failed for "${slug}" — falling back to seed posts:`, err)
    }
  }
  const { getPostBySlug } = await import("./posts")
  const legacy = getPostBySlug(slug)
  return legacy && legacy.frontmatter.status === "published" ? legacy : null
}

/** Prev/next navigation neighbors for a blog detail page. */
export interface AdjacentPosts {
  readonly prev: Pick<BlogPostMeta, "slug" | "title"> | null
  readonly next: Pick<BlogPostMeta, "slug" | "title"> | null
}

function toNavItem(p: BlogPostMeta | undefined): AdjacentPosts["prev"] {
  return p ? { slug: p.slug, title: p.title } : null
}

/**
 * NFM-4942: prev/next navigation lookup. The page used to pass the raw
 * `params.slug` here — still percent-encoded for non-ASCII slugs — while
 * the published list carries decoded slugs, so `findIndex` returned -1
 * and the nav silently rendered nothing. Decode the input here so the
 * lookup is safe regardless of which form a caller hands over.
 */
export async function findAdjacentPosts(currentSlugInput: string): Promise<AdjacentPosts> {
  const currentSlug = decodeSlug(currentSlugInput)
  const posts = await getPublishedPosts()
  const currentIndex = posts.findIndex((p) => p.slug === currentSlug)

  if (currentIndex === -1) {
    return { prev: null, next: null }
  }

  return {
    prev: currentIndex > 0 ? toNavItem(posts[currentIndex - 1]) : null,
    next: currentIndex < posts.length - 1 ? toNavItem(posts[currentIndex + 1]) : null,
  }
}
