import { NextResponse } from "next/server"
import fs from "node:fs"
import path from "node:path"
import matter from "gray-matter"

function getContentDir(): string {
  return process.env.BLOG_CONTENT_DIR || path.join(process.cwd(), "content", "blog")
}

export async function GET() {
  try {
    const contentDir = getContentDir()

    if (!fs.existsSync(contentDir)) {
      return NextResponse.json({ success: true, data: { count: 0 } })
    }

    const files = fs.readdirSync(contentDir).filter((file) => file.endsWith(".md"))

    const underReviewCount = files.reduce((count, file) => {
      const filePath = path.join(contentDir, file)
      const raw = fs.readFileSync(filePath, "utf-8")
      const { data } = matter(raw)
      return data.status === "under_review" ? count + 1 : count
    }, 0)

    return NextResponse.json({
      success: true,
      data: { count: underReviewCount },
    })
  } catch (error) {
    return NextResponse.json(
      { success: false, error: "Failed to fetch review count" },
      { status: 500 },
    )
  }
}
