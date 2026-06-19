"use client"

import Link from "next/link"

export default function HomePage() {
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
    </main>
  )
}
