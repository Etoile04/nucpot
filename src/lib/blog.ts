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
  ogImage: string
  content: string
}

const CONTENT_DIR = path.join(process.cwd(), 'content', 'blog')

function readMarkdownFile(filePath: string): Post | null {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8')
    const { data, content } = matter(raw)

    const slug = path.basename(filePath, '.md')

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
      ogImage:
        typeof data.ogImage === 'string' ? data.ogImage : '',
      content,
    }
  } catch {
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
  const filePath = path.join(CONTENT_DIR, `${slug}.md`)
  return readMarkdownFile(filePath)
}

export function getAllSlugs(): string[] {
  if (!fs.existsSync(CONTENT_DIR)) {
    return []
  }

  return fs
    .readdirSync(CONTENT_DIR)
    .filter((f) => f.endsWith('.md'))
    .map((f) => path.basename(f, '.md'))
}
