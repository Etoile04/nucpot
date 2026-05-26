interface SkeletonCardProps {
  count?: number
}

export default function SkeletonCard({ count = 6 }: SkeletonCardProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-gray-800/50 rounded-xl p-4 border border-gray-700 animate-pulse"
        >
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              <div className="h-5 w-48 bg-gray-700 rounded mb-3" />
              <div className="flex gap-2 mb-3">
                <div className="h-5 w-12 bg-gray-700 rounded" />
                <div className="h-5 w-16 bg-gray-700 rounded" />
                <div className="h-5 w-20 bg-gray-700 rounded" />
              </div>
              <div className="h-4 w-full bg-gray-700 rounded mb-1" />
              <div className="h-4 w-3/4 bg-gray-700 rounded" />
            </div>
            <div className="ml-4 shrink-0 flex items-center gap-3">
              <div className="h-4 w-12 bg-gray-700 rounded" />
              <div className="h-6 w-14 bg-gray-700 rounded" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
