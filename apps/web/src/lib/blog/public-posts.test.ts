/**
 * NFM-4940 regression tests: the public-blog data layer runs in server
 * components (SSR), where a relative fetch path throws in Node
 * (`Failed to parse URL from /api/v1/...`). The old silent catch then fell
 * back to the FS seed posts — empty in prod — so no admin-published post
 * ever appeared publicly. SSR fetches must go through an absolute
 * API_SERVER_URL base (same contract as kg-graph-api.ts).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { getPublishedPost, getPublishedPosts } from "./public-posts"

// The legacy FS seed loader reads `fs`/`path`, which vitest's jsdom sandbox
// cannot import (ERR_UNKNOWN_BUILTIN_MODULE). Mock it with one seed post so
// the fallback path is observable without touching the filesystem.
vi.mock("./posts", () => ({
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
  getPostBySlug: (slug: string) =>
    slug === "seed-post"
      ? {
          slug: "seed-post",
          frontmatter: {
            title: "FS Seed Post",
            date: "2026-01-01",
            summary: "legacy markdown seed",
            tags: ["seed"],
            author: "Seed",
            status: "published",
          },
          content: "# seed body",
        }
      : null,
  getAllSlugs: () => ["seed-post"],
}))

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

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  } as unknown as Response
}

/**
 * Behaves like real Node fetch: relative URLs are unparseable and throw,
 * absolute URLs return the DB payload. This mirrors the server context the
 * defect was filed against — a mock that accepted any URL would hide it.
 *
 * NFM-4940 residual: the payload is BARE (array for the list endpoint,
 * object for the detail endpoint), matching the FastAPI response_model in
 * apps/api/src/nfm_db/api/v1/blog.py. The first round of tests mocked
 * `{ success, data }` — a contract the real API never spoke — so the
 * suite passed while prod rendered 暂无文章.
 */
function nodeLikeFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : String(input)
    if (!/^https?:\/\//.test(url)) {
      throw new TypeError(`Failed to parse URL from ${url}`)
    }
    return jsonResponse([DB_POST])
  })
}

describe("getPublishedPosts (SSR absolute URL)", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = nodeLikeFetch()
    vi.stubGlobal("fetch", fetchMock)
    process.env.API_SERVER_URL = "http://test-api:8000"
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("fetches the public API via the absolute API_SERVER_URL base", async () => {
    const posts = await getPublishedPosts()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://test-api:8000/api/v1/blog/public")
    expect(posts).toHaveLength(1)
    expect(posts[0]).toMatchObject({
      slug: "db-published-post",
      title: "DB Published Post",
      date: "2026-09-18",
      status: "published",
    })
  })

  it("defaults to the Docker-internal service DNS when API_SERVER_URL is unset", async () => {
    delete process.env.API_SERVER_URL

    await getPublishedPosts()

    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://nucpot-prod-api:8000/api/v1/blog/public")
  })

  it("logs the error and falls back when the fetch throws (no silent catch)", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to parse URL from /api/v1/blog/public"))

    const posts = await getPublishedPosts()

    expect(errSpy).toHaveBeenCalled()
    // Falls back to the (mocked) FS seed posts — in prod the seeds are
    // empty, which is exactly the 暂无文章 failure mode filed here.
    expect(posts).toHaveLength(1)
    expect(posts[0]?.slug).toBe("seed-post")
  })

  it("logs a non-OK response before falling back", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({}),
    } as unknown as Response)

    const posts = await getPublishedPosts()

    expect(errSpy).toHaveBeenCalled()
    expect(posts).toHaveLength(1)
    expect(posts[0]?.slug).toBe("seed-post")
  })

  it("still parses a { data: [...] } envelope if the API ever wraps", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [DB_POST] }))

    const posts = await getPublishedPosts()

    expect(posts).toHaveLength(1)
    expect(posts[0]?.slug).toBe("db-published-post")
  })
})

describe("getPublishedPost (SSR absolute URL)", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : String(input)
      if (!/^https?:\/\//.test(url)) {
        throw new TypeError(`Failed to parse URL from ${url}`)
      }
      return jsonResponse(DB_POST)
    })
    vi.stubGlobal("fetch", fetchMock)
    process.env.API_SERVER_URL = "http://test-api:8000"
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete process.env.API_SERVER_URL
    vi.restoreAllMocks()
  })

  it("fetches one post via the absolute base and maps the bare DTO", async () => {
    const post = await getPublishedPost("db-published-post")

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://test-api:8000/api/v1/blog/public/db-published-post",
    )
    expect(post).not.toBeNull()
    expect(post?.frontmatter.title).toBe("DB Published Post")
    expect(post?.content).toBe("Body from the database")
  })

  it("still parses a { data: {...} } envelope if the API ever wraps", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: DB_POST }))

    const post = await getPublishedPost("db-published-post")

    expect(post?.frontmatter.title).toBe("DB Published Post")
  })

  it("decodes the Next-encoded slug param exactly once (prod regression)", async () => {
    // Next 16 hands /blog/<chinese> over with params.slug still
    // percent-encoded. encodeURIComponent on top of that double-encodes
    // (%25E6...) → the API decodes one layer → literal "%E6..." slug →
    // 404 → prod detail pages died exactly here.
    const canonical = "技术总结报告测试文章自动化验证-1788093545"
    const encoded = encodeURIComponent(canonical)

    const post = await getPublishedPost(encoded)

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `http://test-api:8000/api/v1/blog/public/${encoded}`,
    )
    expect(post?.frontmatter.title).toBe("DB Published Post")
  })

  it("falls back to the raw slug when decoding fails (malformed %)", async () => {
    const post = await getPublishedPost("100%-raw-slug")

    // "%-r" is not a valid escape: decodeSlug keeps the raw value, and
    // encodeURIComponent still makes it path-safe ("100%25-raw-slug").
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://test-api:8000/api/v1/blog/public/100%25-raw-slug",
    )
    expect(post?.frontmatter.title).toBe("DB Published Post")
  })

  it("returns null (logged) when the fetch fails, instead of throwing", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    fetchMock.mockRejectedValueOnce(
      new TypeError("Failed to parse URL from /api/v1/blog/public/db-published-post"),
    )

    const post = await getPublishedPost("db-published-post")

    expect(errSpy).toHaveBeenCalled()
    expect(post).toBeNull()
  })
})
