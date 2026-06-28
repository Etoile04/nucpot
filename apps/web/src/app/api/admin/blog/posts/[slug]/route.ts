import { NextRequest, NextResponse } from "next/server"
import fs from "node:fs"
import path from "node:path"
import matter from "gray-matter"

function getContentDir(): string {
  return process.env.BLOG_CONTENT_DIR || path.join(process.cwd(), "content", "blog")
}

function getFilePath(slug: string): string {
  return path.join(getContentDir(), `${slug}.md`)
}

function validateSlug(slug: string): boolean {
  // Prevent path traversal
  if (slug.includes("..") || slug.includes("/") || slug.includes("\\")) {
    return false
  }
  // Only allow alphanumeric, hyphens, and underscores
  return /^[a-zA-Z0-9-_]+$/.test(slug)
}

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  try {
    const { slug } = await params

    if (!validateSlug(slug)) {
      return NextResponse.json(
        { success: false, error: "Invalid slug" },
        { status: 400 }
      )
    }

    const filePath = getFilePath(slug)

    if (!fs.existsSync(filePath)) {
      return NextResponse.json(
        { success: false, error: "文章不存在" },
        { status: 404 }
      )
    }

    const raw = fs.readFileSync(filePath, "utf-8")
    const { data, content } = matter(raw)

    return NextResponse.json({
      success: true,
      data: {
        slug,
        title: data.title,
        date: data.date,
        author: data.author,
        tags: data.tags,
        summary: data.summary,
        content,
      },
    })
  } catch (error) {
    console.error("Error reading blog post:", error)
    return NextResponse.json(
      { success: false, error: "读取文章失败" },
      { status: 500 }
    )
  }
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  try {
    const { slug } = await params
    const body = await request.json()
    const { title, author, tags, summary, content } = body

    if (!validateSlug(slug)) {
      return NextResponse.json(
        { success: false, error: "Invalid slug" },
        { status: 400 }
      )
    }

    if (!title || !author || !summary || !content) {
      return NextResponse.json(
        { success: false, error: "Missing required fields" },
        { status: 400 }
      )
    }

    const filePath = getFilePath(slug)

    if (!fs.existsSync(filePath)) {
      return NextResponse.json(
        { success: false, error: "文章不存在" },
        { status: 404 }
      )
    }

    // Create frontmatter (preserve original date)
    const existingRaw = fs.readFileSync(filePath, "utf-8")
    const { data: existingData } = matter(existingRaw)

    const frontmatter = matter.stringify(content, {
      title,
      date: existingData.date || new Date().toISOString().split("T")[0],
      author,
      tags: Array.isArray(tags) ? tags : tags.split(",").map((t: string) => t.trim()),
      summary,
    })

    await fs.promises.writeFile(filePath, frontmatter, "utf-8")

    return NextResponse.json({
      success: true,
      data: { slug, message: "文章更新成功" }
    })
  } catch (error) {
    console.error("Error updating blog post:", error)
    return NextResponse.json(
      { success: false, error: "更新文章失败" },
      { status: 500 }
    )
  }
}

export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  try {
    const { slug } = await params

    if (!validateSlug(slug)) {
      return NextResponse.json(
        { success: false, error: "Invalid slug" },
        { status: 400 }
      )
    }

    const filePath = getFilePath(slug)

    if (!fs.existsSync(filePath)) {
      return NextResponse.json(
        { success: false, error: "文章不存在" },
        { status: 404 }
      )
    }

    await fs.promises.unlink(filePath)

    return NextResponse.json({
      success: true,
      data: { slug, message: "文章删除成功" }
    })
  } catch (error) {
    console.error("Error deleting blog post:", error)
    return NextResponse.json(
      { success: false, error: "删除文章失败" },
      { status: 500 }
    )
  }
}
