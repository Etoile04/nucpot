import Link from 'next/link'

export default function NotFound() {
  return (
    <div className="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center px-4">
      <h1 className="text-8xl font-bold text-gray-600 mb-4">404</h1>
      <p className="text-xl text-gray-400 mb-8">页面不存在</p>
      <div className="flex gap-4">
        <Link
          href="/"
          className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition"
        >
          返回首页
        </Link>
        <Link
          href="/browse"
          className="px-6 py-2.5 border border-gray-600 hover:border-gray-400 rounded-lg text-sm font-medium transition"
        >
          浏览势函数
        </Link>
      </div>
    </div>
  )
}
