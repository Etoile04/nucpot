import { NextRequest, NextResponse } from "next/server"
import fs from "node:fs"
import path from "node:path"
import matter from "gray-matter"

function getContentDir(): string {
  return process.env.BLOG_CONTENT_DIR || path.join(process.cwd(), "content", "blog")
}

function sanitizeSlug(slug: string): string {
  // Remove special characters, replace spaces with hyphens, convert to lowercase
  return slug
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
}

function generateSlug(title: string): string {
  const timestamp = Date.now()
  const sanitized = sanitizeSlug(title)
  return `${sanitized}-${timestamp}`
}

async function ensureContentDir(): Promise<void> {
  const contentDir = getContentDir()
  if (!fs.existsSync(contentDir)) {
    await fs.promises.mkdir(contentDir, { recursive: true })
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { title, author, tags, summary, content } = body

    // Validate required fields
    if (!title || !author || !summary || !content) {
      return NextResponse.json(
        { success: false, error: "Missing required fields" },
        { status: 400 }
      )
    }

    // Generate slug from title
    const slug = generateSlug(title)

    // Create frontmatter
    const frontmatter = matter.stringify(content, {
      title,
      date: new Date().toISOString().split("T")[0],
      author,
      tags: Array.isArray(tags) ? tags : tags.split(",").map((t: string) => t.trim()),
      summary,
    })

    // Ensure content directory exists
    await ensureContentDir()

    // Write file
    const filePath = path.join(getContentDir(), `${slug}.md`)
    await fs.promises.writeFile(filePath, frontmatter, "utf-8")

    return NextResponse.json({
      success: true,
      data: { slug, message: "文章创建成功" }
    })
  } catch (error) {
    console.error("Error creating blog post:", error)
    return NextResponse.json(
      { success: false, error: "创建文章失败" },
      { status: 500 }
    )
  }
}

export async function GET() {
  try {
    const contentDir = getContentDir()

    if (!fs.existsSync(contentDir)) {
      return NextResponse.json({ success: true, data: [] })
    }

    const files = fs.readdirSync(contentDir).filter((file) => file.endsWith(".md"))

    const posts = files.map((file) => {
      const filePath = path.join(contentDir, file)
      const raw = fs.readFileSync(filePath, "utf-8")
      const { data } = matter(raw)

      return {
        slug: file.replace(/\.md$/, ""),
        title: data.title,
        date: data.date,
        author: data.author,
        tags: data.tags,
        summary: data.summary,
      }
    })

    // Sort by date descending
    posts.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())

    return NextResponse.json({ success: true, data: posts })
  } catch (error) {
    console.error("Error reading blog posts:", error)
    return NextResponse.json(
      { success: false, error: "读取文章列表失败" },
      { status: 500 }
    )
  }
}
