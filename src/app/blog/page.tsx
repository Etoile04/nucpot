import type { Metadata } from 'next'
import Link from 'next/link'
import { getAllPosts, formatDate } from '@/lib/blog'
import Footer from '@/components/Footer'

export const metadata: Metadata = {
  title: '博客 — NucPot',
  description:
    '核材料势函数库技术博客：势函数建模方法、材料性能分析、模拟技巧分享。',
  openGraph: {
    title: '博客 — NucPot 核材料势函数库',
    description: '核材料势函数库技术博客',
    url: 'https://nucpot.vercel.app/blog',
    siteName: 'NucPot',
    locale: 'zh_CN',
    type: 'website',
  },
}

export default function BlogPage() {
  const posts = getAllPosts()

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12">
        <header className="mb-12">
          <h1 className="text-3xl font-bold mb-2">博客</h1>
          <p className="text-gray-400 text-lg">
            势函数建模、材料性能分析与模拟技巧
          </p>
        </header>

        {posts.length === 0 ? (
          <p className="text-gray-500">暂无文章，敬请期待。</p>
        ) : (
          <ul className="space-y-8">
            {posts.map((post) => (
              <li key={post.slug}>
                <article className="group">
                  <Link href={`/blog/${post.slug}`} className="block">
                    <div className="border border-gray-700 rounded-lg p-6 transition-colors group-hover:border-blue-500/50 group-hover:bg-gray-800/50">
                      <div className="flex items-center gap-3 mb-2 text-sm text-gray-500">
                        <time dateTime={post.date}>
                          {formatDate(post.date)}
                        </time>
                        {post.author && (
                          <>
                            <span aria-hidden="true">·</span>
                            <span>{post.author}</span>
                          </>
                        )}
                      </div>
                      <h2 className="text-xl font-semibold text-white group-hover:text-blue-400 transition-colors mb-2">
                        {post.title}
                      </h2>
                      <p className="text-gray-400 leading-relaxed line-clamp-2">
                        {post.description}
                      </p>
                      {post.tags.length > 0 && (
                        <div className="flex flex-wrap gap-2 mt-4">
                          {post.tags.map((tag) => (
                            <span
                              key={tag}
                              className="text-xs px-2 py-1 rounded bg-gray-800 text-gray-400 border border-gray-700"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </Link>
                </article>
              </li>
            ))}
          </ul>
        )}
      </main>

      <Footer />
    </div>
  )
}
