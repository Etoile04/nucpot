/**
 * NFM-4940 SSR regression: the rendered /blog list must surface
 * DB-published posts. The old data layer fetched a relative URL from the
 * server component, Node fetch threw, the silent catch fell back to the FS
 * seeds (empty in prod), and the page rendered 暂无文章 — so publishing from
 * admin never showed up publicly. This test renders the real list page
 * through the real data layer with a Node-faithful fetch mock.
 */
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// The list page renders client components that expect the Next router.
// Render them inert so the SSR data path stays the subject under test.
vi.mock("next/navigation", () => ({
  usePathname: () => "/blog",
}))
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))
// The legacy FS seed loader reads `fs`/`path`, which vitest's jsdom sandbox
// cannot import (ERR_UNKNOWN_BUILTIN_MODULE). Mock it so the data layer's
// fallback path resolves without touching the filesystem.
vi.mock("@/lib/blog/posts", () => ({
  getAllPosts: () => [
    {
      slug: "seed-post",
      title: "FS Seed Post",
      date: "2026-01-01",
      summary: "legacy markdown seed",
      tags: ["seed"],
      author: "Seed",
      status: "published",
    },
  ],
  getPostBySlug: () => null,
  getAllSlugs: () => ["seed-post"],
}))

import BlogListPage from "./page"

const DB_POST = {
  slug: "db-published-post",
  title: "DB Published Post",
  summary: "published via the admin workflow",
  tags: ["ssr"],
  author_name: "QA",
  published_at: "2026-09-18T00:00:00Z",
  created_at: "2026-09-17T00:00:00Z",
  content: "Body from the database",
}

describe("BlogListPage SSR (NFM-4940)", () => {
  beforeEach(() => {
    // Behave like real Node fetch: a relative URL is unparseable and throws.
    // A permissive mock would let the old relative-URL bug pass this test.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === "string" ? input : String(input)
        if (!/^https?:\/\//.test(url)) {
          throw new TypeError(`Failed to parse URL from ${url}`)
        }
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({ success: true, data: [DB_POST] }),
        } as unknown as Response
      }),
    )
    process.env.API_SERVER_URL = "http://test-api:8000"
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("renders a DB-published post card instead of the empty state", async () => {
    const tree = await BlogListPage()
    const html = renderToStaticMarkup(tree)

    expect(html).toContain("DB Published Post")
    expect(html).toContain('href="/blog/db-published-post"')
    expect(html).not.toContain("暂无文章")
  })
})
