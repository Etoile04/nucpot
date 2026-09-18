/**
 * NFM-4942 regression: Next 16 hands dynamic-route params over still
 * percent-encoded, so a non-ASCII slug reached findAdjacentPosts encoded
 * while the published list carries decoded slugs — findIndex returned -1
 * and the prev/next nav silently rendered nothing. The raw param also
 * missed the FS-seed index, making the `?? getPostBySlug(slug)` fallback
 * dead for non-ASCII slugs. This renders the real detail page through the
 * real data layer with a Node-faithful fetch mock and the encoded param
 * exactly as Next delivers it.
 */
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { BlogPost } from "@/lib/blog/types"

const CANONICAL = vi.hoisted(() => "技术总结报告测试文章自动化验证-1788093545")

// Capture FS-lookup calls so tests can assert the DECODED slug reaches
// getPostBySlug (the fallback that was dead for non-ASCII slugs).
const seedMocks = vi.hoisted(() => ({
  getPostBySlug: vi.fn<(slug: string) => BlogPost | null>(() => null),
}))

// Client components expect the Next router; render them inert so the SSR
// data path stays the subject under test.
vi.mock("next/navigation", () => ({
  usePathname: () => `/blog/${encodeURIComponent(CANONICAL)}`,
  notFound: () => {
    throw new Error("notFound() reached")
  },
}))
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))
// The legacy FS seed loader reads `fs`/`path`, which vitest's jsdom sandbox
// cannot import (ERR_UNKNOWN_BUILTIN_MODULE).
vi.mock("@/lib/blog/posts", () => ({
  getAllPosts: () => [],
  getPostBySlug: seedMocks.getPostBySlug,
  getAllSlugs: () => [],
}))

import BlogDetailPage from "./page"

const LIST = [
  {
    slug: "first-post",
    title: "First Post",
    summary: null,
    tags: ["a"],
    author_name: "QA",
    published_at: "2026-09-16T00:00:00Z",
    created_at: "2026-09-16T00:00:00Z",
    content: null,
  },
  {
    slug: CANONICAL,
    title: "中文 Slug 文章",
    summary: null,
    tags: ["zh"],
    author_name: "QA",
    published_at: "2026-09-17T00:00:00Z",
    created_at: "2026-09-17T00:00:00Z",
    content: "正文内容",
  },
  {
    slug: "last-post",
    title: "Last Post",
    summary: null,
    tags: ["z"],
    author_name: "QA",
    published_at: "2026-09-18T00:00:00Z",
    created_at: "2026-09-18T00:00:00Z",
    content: null,
  },
]

/** Node-faithful fetch: bare payloads (FastAPI response_model), absolute URLs only. */
function stubNodeFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : String(input)
      if (!/^https?:\/\//.test(url)) {
        throw new TypeError(`Failed to parse URL from ${url}`)
      }
      const encoded = encodeURIComponent(CANONICAL)
      const body = url.endsWith(`/api/v1/blog/public/${encoded}`) ? LIST[1] : LIST
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => body,
      } as unknown as Response
    }),
  )
}

describe("BlogDetailPage prev/next nav (NFM-4942)", () => {
  beforeEach(() => {
    seedMocks.getPostBySlug.mockReset()
    seedMocks.getPostBySlug.mockImplementation(() => null)
    stubNodeFetch()
    process.env.API_SERVER_URL = "http://test-api:8000"
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("renders working prev/next links for a Next-encoded non-ASCII slug (AC-1)", async () => {
    const tree = await BlogDetailPage({
      params: Promise.resolve({ slug: encodeURIComponent(CANONICAL) }),
    })
    const html = renderToStaticMarkup(tree)

    // The post itself resolved (decode-once, NFM-4940) …
    expect(html).toContain("中文 Slug 文章")
    // … and so did its neighbors (the NFM-4942 fix).
    expect(html).toContain('href="/blog/first-post"')
    expect(html).toContain('href="/blog/last-post"')
    expect(html).toContain("← 上一篇")
    expect(html).toContain("下一篇 →")
  })

  it("passes the DECODED slug to the FS-seed fallback (previously dead)", async () => {
    // Detail endpoint 404s and the FS seed is a draft, so getPublishedPost
    // itself returns null — the page's own `?? getPostBySlug(slug)` fallback
    // must run, and it must run with the DECODED slug so the FS index can
    // match. Pre-fix the raw encoded param missed the index → notFound().
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === "string" ? input : String(input)
        if (!/^https?:\/\//.test(url)) {
          throw new TypeError(`Failed to parse URL from ${url}`)
        }
        if (url.includes(`/api/v1/blog/public/${encodeURIComponent(CANONICAL)}`)) {
          return {
            ok: false,
            status: 404,
            statusText: "Not Found",
            json: async () => ({}),
          } as unknown as Response
        }
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => LIST,
        } as unknown as Response
      }),
    )
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    seedMocks.getPostBySlug.mockImplementation((slug: string) =>
      slug === CANONICAL
        ? {
            slug: CANONICAL,
            frontmatter: {
              title: "FS Seed 中文文章",
              date: "2026-09-17",
              summary: "legacy markdown seed",
              tags: ["seed"],
              author: "Seed",
              status: "draft",
            },
            content: "# seed body",
          }
        : null,
    )

    const tree = await BlogDetailPage({
      params: Promise.resolve({ slug: encodeURIComponent(CANONICAL) }),
    })
    const html = renderToStaticMarkup(tree)

    expect(seedMocks.getPostBySlug).toHaveBeenCalledWith(CANONICAL)
    expect(seedMocks.getPostBySlug).not.toHaveBeenCalledWith(encodeURIComponent(CANONICAL))
    expect(html).toContain("FS Seed 中文文章")
    expect(errSpy).toHaveBeenCalled()
  })
})
