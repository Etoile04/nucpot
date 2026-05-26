export default function Loading() {
  return (
    <div className="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center">
      <div className="w-10 h-10 border-4 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
      <p className="mt-4 text-gray-400 text-sm">加载中...</p>
    </div>
  )
}
