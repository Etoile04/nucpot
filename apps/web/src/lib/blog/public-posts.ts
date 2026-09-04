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
import { request } from '@/lib/api-client'
import type { BlogPost, BlogPostMeta } from './types'

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
    summary: p.summary ?? '',
    tags: p.tags ?? [],
    author: p.author_name ?? 'NucPot',
    status: 'published',
  }
}

function toPost(p: PublicPostDto): BlogPost {
  return { slug: p.slug, frontmatter: toMeta(p), content: p.content ?? '' }
}

/**
 * During `next build` no API is reachable, and a relative-URL fetch against
 * nothing can stall past the 60s per-page build timeout (every /blog page
 * then fails after 3 retries). Serve the FS seeds at build time; ISR picks
 * up live DB content on the first runtime revalidation.
 */
function isProductionBuild(): boolean {
  return process.env.NEXT_PHASE === 'phase-production-build'
}

/** Upper bound for one public-blog API call (ISR revalidation included). */
const FETCH_TIMEOUT_MS = 10_000

/** Fetch published posts from the public API; fall back to FS seed posts. */
export async function getPublishedPosts(): Promise<readonly BlogPostMeta[]> {
  if (!isProductionBuild()) {
    try {
      const res = await request<{ success: boolean; data: PublicPostDto[] }>(
        '/api/v1/blog/public',
        { next: { revalidate: 60 }, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) } as never,
      )
      const items = (res.data ?? []).map(toMeta)
      if (items.length > 0) return items
    } catch {
      // API unavailable → fall through to the FS seed posts below.
    }
  }
  // Legacy fallback: build-time markdown seeds (no dynamic import cycle).
  const { getAllPosts } = await import('./posts')
  return getAllPosts().filter((p) => p.status === 'published')
}

/** Fetch one published post by slug (null when missing/unpublished). */
export async function getPublishedPost(
  slug: string,
): Promise<BlogPost | null> {
  if (!isProductionBuild()) {
    try {
      const res = await request<{ success: boolean; data: PublicPostDto }>(
        `/api/v1/blog/public/${encodeURIComponent(slug)}`,
        { next: { revalidate: 60 }, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) } as never,
      )
      if (res.data?.slug) return toPost(res.data)
    } catch {
      // fall through to legacy seeds
    }
  }
  const { getPostBySlug } = await import('./posts')
  const legacy = getPostBySlug(slug)
  return legacy && legacy.frontmatter.status === 'published' ? legacy : null
}
