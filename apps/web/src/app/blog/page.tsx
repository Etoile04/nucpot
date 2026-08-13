import type { Metadata } from "next"
import { getAllPosts } from "@/lib/blog/posts"
import type { BlogPostMeta } from "@/lib/blog/types"
import { BlogCard, BlogSidebar } from "@/components/blog"
import "./blog.css"

export const metadata: Metadata = {
  title: "技术博客 — 核燃料与材料物性数据库",
  description: "核材料科学、数据库技术、核工程领域的技术文章",
}

function extractUniqueTags(
  posts: readonly BlogPostMeta[]
): readonly string[] {
  const tagSet = new Set<string>()
  for (const post of posts) {
    for (const tag of post.tags) {
      tagSet.add(tag)
    }
  }
  return [...tagSet].sort()
}

export default function BlogListPage() {
  const posts = getAllPosts()
  const allTags = extractUniqueTags(posts)

  return (
    <>
      <BlogSidebar posts={posts} />
      <main className="blog-container">
        <header className="blog-header">
        <h1 className="blog-title">技术博客</h1>
        <p className="blog-description">
          核材料科学、数据库技术与核工程领域的技术文章
        </p>
      </header>

      <nav className="blog-tag-filter" aria-label="文章标签筛选">
        <div className="blog-filter-label">按标签筛选</div>
        <div className="blog-tags-list">
          {allTags.map((tag) => (
            <span key={tag} className="blog-tag">
              {tag}
            </span>
          ))}
        </div>
      </nav>

      {posts.length === 0 ? (
        <div className="blog-empty">
          <p>暂无文章</p>
        </div>
      ) : (
        <div className="blog-list">
          {posts.map((post) => (
            <BlogCard key={post.slug} post={post} />
          ))}
        </div>
      )}
      </main>
    </>
  )
}
