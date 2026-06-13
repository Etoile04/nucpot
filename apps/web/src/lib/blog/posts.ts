import fs from "fs"
import path from "path"
import matter from "gray-matter"
import type { BlogPost, BlogPostMeta, BlogPostFrontmatter } from "./types"

function normalizeDate(value: unknown): string {
  if (value instanceof Date) {
    const iso = value.toISOString()
    return iso.split("T")[0] ?? iso
  }
  return String(value)
}

const REQUIRED_FIELDS = ["title", "date", "summary", "tags", "author"] as const

type RequiredField = (typeof REQUIRED_FIELDS)[number]

function getContentDir(): string {
  return process.env.BLOG_CONTENT_DIR || path.join(process.cwd(), "content", "blog")
}

function isValidFrontmatter(data: unknown): data is BlogPostFrontmatter {
  if (typeof data !== "object" || data === null) {
    return false
  }

  const record = data as Record<string, unknown>

  return REQUIRED_FIELDS.every(
    (field: RequiredField) =>
      field in record &&
      record[field] !== null &&
      record[field] !== undefined &&
      (field === "tags"
        ? Array.isArray(record[field])
        : field === "date"
          ? record[field] instanceof Date || typeof record[field] === "string"
          : typeof record[field] === "string")
  )
}

function parseMarkdownFile(
  filePath: string,
  fileName: string
): BlogPost | null {
  try {
    const raw = fs.readFileSync(filePath, "utf-8")
    const { data, content } = matter(raw)

    if (!isValidFrontmatter(data)) {
      return null
    }

    return {
      slug: fileName.replace(/\.md$/, ""),
      frontmatter: {
        title: String(data.title),
        date: normalizeDate(data.date),
        summary: String(data.summary),
        tags: Array.isArray(data.tags) ? [...data.tags] : [],
        author: String(data.author),
      },
      content,
    }
  } catch {
    return null
  }
}

function listMarkdownFiles(contentDir: string): readonly string[] {
  if (!fs.existsSync(contentDir)) {
    return []
  }

  const files = fs.readdirSync(contentDir)
  return files.filter((file) => file.endsWith(".md"))
}

function getAllPostsInternal(
  contentDir: string
): readonly BlogPost[] {
  const mdFiles = listMarkdownFiles(contentDir)

  const posts = mdFiles
    .map((file) =>
      parseMarkdownFile(path.join(contentDir, file), file)
    )
    .filter((post): post is BlogPost => post !== null)

  return [...posts].sort(
    (a, b) =>
      new Date(b.frontmatter.date).getTime() -
      new Date(a.frontmatter.date).getTime()
  )
}

function toMeta(post: BlogPost): BlogPostMeta {
  return {
    slug: post.slug,
    title: post.frontmatter.title,
    date: post.frontmatter.date,
    summary: post.frontmatter.summary,
    tags: post.frontmatter.tags,
    author: post.frontmatter.author,
    status: post.frontmatter.status,
  }
}

export function getAllPosts(): readonly BlogPostMeta[] {
  return getAllPostsInternal(getContentDir())
    .filter((post) => {
      // Filter to show only published posts
      // Check if status field exists in frontmatter and is 'published'
      const status = post.frontmatter.status
      return !status || status === 'published'
    })
    .map(toMeta)
}

export function getPostBySlug(slug: string): BlogPost | null {
  const contentDir = getContentDir()

  if (!fs.existsSync(contentDir)) {
    return null
  }

  const filePath = path.resolve(contentDir, `${slug}.md`)
  const resolvedContentDir = path.resolve(contentDir)

  if (!filePath.startsWith(resolvedContentDir + path.sep)) {
    return null
  }

  if (!fs.existsSync(filePath)) {
    return null
  }

  return parseMarkdownFile(filePath, `${slug}.md`)
}

export function getAllSlugs(): readonly string[] {
  return getAllPostsInternal(getContentDir()).map((post) => post.slug)
}

// Exported for testing
export { getContentDir, parseMarkdownFile, listMarkdownFiles }
