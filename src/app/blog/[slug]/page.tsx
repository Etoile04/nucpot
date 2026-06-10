import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import { getAllPosts, getPostBySlug } from '@/lib/blog'
import Footer from '@/components/Footer'

interface PageProps {
  params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const posts = getAllPosts()
  return posts.map((post) => ({ slug: post.slug }))
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params
  const post = getPostBySlug(slug)

  if (!post) {
    return { title: '文章未找到 — NucPot' }
  }

  return {
    title: `${post.title} — NucPot`,
    description: post.description,
    openGraph: {
      title: post.title,
      description: post.description,
      url: `https://nucpot.vercel.app/blog/${post.slug}`,
      siteName: 'NucPot',
      locale: 'zh_CN',
      type: 'article',
      publishedTime: post.date,
      authors: [post.author],
      ...(post.ogImage && { images: [{ url: post.ogImage }] }),
    },
  }
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default async function BlogPostPage({ params }: PageProps) {
  const { slug } = await params
  const post = getPostBySlug(slug)

  if (!post) {
    notFound()
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12">
        <article className="space-y-8">
          <header>
            <Link
              href="/blog"
              className="inline-flex items-center text-sm text-gray-400 hover:text-blue-400 transition-colors mb-6"
            >
              <span aria-hidden="true">←</span>
              <span className="ml-1">返回博客</span>
            </Link>

            <h1 className="text-3xl font-bold mb-4">{post.title}</h1>

            <div className="flex items-center gap-3 text-sm text-gray-500">
              <time dateTime={post.date}>{formatDate(post.date)}</time>
              {post.author && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{post.author}</span>
                </>
              )}
            </div>

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
          </header>

          <hr className="border-gray-700" />

          <section className="prose prose-invert prose-gray max-w-none prose-headings:text-white prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg prose-p:text-gray-300 prose-li:text-gray-300 prose-strong:text-white prose-a:text-blue-400 prose-code:text-blue-300 prose-pre:bg-gray-800 prose-pre:border prose-pre:border-gray-700 prose-table:border-gray-700 prose-th:border-gray-700 prose-td:border-gray-700">
            <ReactMarkdown>{post.content}</ReactMarkdown>
          </section>
        </article>
      </main>

      <Footer />
    </div>
  )
}
