'use client'

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error
  reset: () => void
}) {
  return (
    <div className="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center px-4">
      <h1 className="text-4xl font-bold text-red-400 mb-4">出错了</h1>
      <p className="text-gray-400 mb-2 text-center max-w-md">
        {error.message || '发生了意外错误'}
      </p>
      <button
        onClick={reset}
        className="mt-6 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition"
      >
        重试
      </button>
    </div>
  )
}
