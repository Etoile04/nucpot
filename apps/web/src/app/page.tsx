import Link from "next/link"
import { getAllPosts } from "@/lib/blog/posts"
import { formatDate } from "@/lib/blog/format-date"

const LATEST_POSTS_COUNT = 3

export default function HomePage() {
  const allPosts = getAllPosts()
  const latestPosts = allPosts.slice(0, LATEST_POSTS_COUNT)

  return (
    <main className="max-w-[960px] mx-auto px-6 py-16">
      <h1 className="text-4xl font-bold mb-4 tracking-tight">
        核燃料与材料物性数据库
      </h1>
      <p className="text-lg text-gray-300 mb-2">
        可持续共享的核燃料与材料物性数据库平台
      </p>
      <p className="text-gray-400 mb-12">
        Nuclear Fuel &amp; Materials Properties Database — a sustainable and
        sharing platform for nuclear materials data in China.
      </p>

      <section className="p-8 bg-gray-700 border border-gray-600 rounded-lg mb-8">
        <h2 className="text-2xl font-semibold mb-4">
          技术文档
        </h2>
        <p className="text-gray-300 mb-6 leading-relaxed">
          查看使用指南、API 文档和核材料科学领域的技术文章
        </p>
        <Link
          href="/blog"
          className="inline-block px-6 py-3 text-base font-medium text-white bg-blue-500 hover:bg-blue-400 rounded transition-colors duration-150"
        >
          查看技术博客 →
        </Link>
      </section>

      {latestPosts.length > 0 && (
        <section className="mt-12">
          <h2 className="text-2xl font-semibold mb-6">最新文章</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {latestPosts.map((post) => (
              <Link
                key={post.slug}
                href={`/blog/${post.slug}`}
                className="block p-5 bg-gray-700 border border-gray-600 rounded-lg hover:border-blue-400 transition-colors duration-150"
              >
                <h3 className="text-lg font-semibold mb-2 text-gray-100 leading-snug line-clamp-2">
                  {post.title}
                </h3>
                <time
                  dateTime={post.date}
                  className="block text-sm text-gray-400 mb-2"
                >
                  {formatDate(post.date)}
                </time>
                <p className="text-sm text-gray-400 leading-relaxed line-clamp-3 mb-3">
                  {post.summary}
                </p>
                {post.tags.length > 0 && (
                  <span className="inline-block px-2 py-0.5 text-xs rounded bg-gray-600 text-gray-300">
                    {post.tags[0]}
                  </span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
