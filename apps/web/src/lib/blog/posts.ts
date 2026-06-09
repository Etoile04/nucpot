import fs from "node:fs"
import path from "node:path"
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function isValidFrontmatter(data: any): data is BlogPostFrontmatter {
  return REQUIRED_FIELDS.every(
    (field: RequiredField) =>
      field in data &&
      data[field] !== null &&
      data[field] !== undefined &&
      (field === "tags"
        ? Array.isArray(data[field])
        : field === "date"
          ? data[field] instanceof Date || typeof data[field] === "string"
          : typeof data[field] === "string")
  )
}

function parseMarkdownFile(
  filePath: string,
  fileName: string
): BlogPost | null {
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
  }
}

export function getAllPosts(): readonly BlogPostMeta[] {
  return getAllPostsInternal(getContentDir()).map(toMeta)
}

export function getPostBySlug(slug: string): BlogPost | null {
  const contentDir = getContentDir()

  if (!fs.existsSync(contentDir)) {
    return null
  }

  const filePath = path.join(contentDir, `${slug}.md`)

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
