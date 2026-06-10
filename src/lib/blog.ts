import fs from 'fs'
import path from 'path'
import matter from 'gray-matter'

export interface Post {
  slug: string
  title: string
  date: string
  description: string
  author: string
  tags: string[]
  ogImage?: string
  content: string
}

const CONTENT_DIR = path.resolve(process.cwd(), 'content', 'blog')

function readMarkdownFile(filePath: string): Post | null {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8')
    const { data, content } = matter(raw)

    const slug = path.basename(filePath, '.md')

    const rawOgImage = data.ogImage
    const ogImage =
      typeof rawOgImage === 'string' && rawOgImage.length > 0
        ? rawOgImage
        : undefined

    return {
      slug,
      title: typeof data.title === 'string' ? data.title : slug,
      date: typeof data.date === 'string' ? data.date : '',
      description:
        typeof data.description === 'string'
          ? data.description
          : '',
      author:
        typeof data.author === 'string' ? data.author : 'NucPot 团队',
      tags: Array.isArray(data.tags) ? data.tags.map(String) : [],
      ogImage,
      content,
    }
  } catch (error) {
    console.error(`Failed to read markdown file ${filePath}:`, error)
    return null
  }
}

export function getAllPosts(): Post[] {
  if (!fs.existsSync(CONTENT_DIR)) {
    return []
  }

  const files = fs.readdirSync(CONTENT_DIR).filter((f) => f.endsWith('.md'))

  const posts = files
    .map((file) => readMarkdownFile(path.join(CONTENT_DIR, file)))
    .filter((post): post is Post => post !== null)
    .sort((a, b) => (a.date > b.date ? -1 : 1))

  return posts
}

export function getPostBySlug(slug: string): Post | null {
  if (slug.includes('/') || slug.includes('\\') || slug.includes('..')) {
    return null
  }

  const filePath = path.resolve(CONTENT_DIR, `${slug}.md`)

  if (!filePath.startsWith(CONTENT_DIR)) {
    return null
  }

  return readMarkdownFile(filePath)
}

export function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}
