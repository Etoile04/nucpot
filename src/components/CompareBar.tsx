'use client'

import { useRouter } from 'next/navigation'

interface CompareBarProps {
  selectedIds: string[]
  potentials: Record<string, { name: string; display_name?: string | null }>
  onRemove: (id: string) => void
  onClear: () => void
}

export default function CompareBar({ selectedIds, potentials, onRemove, onClear }: CompareBarProps) {
  const router = useRouter()

  if (selectedIds.length === 0) return null

  const canCompare = selectedIds.length >= 2 && selectedIds.length <= 4

  const handleCompare = () => {
    if (!canCompare) return
    router.push(`/compare?ids=${selectedIds.join(',')}`)
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-800/80 border-t border-gray-700 backdrop-blur-lg">
      <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 flex-1 min-w-0 overflow-x-auto">
          <span className="text-sm text-gray-400 shrink-0">
            已选 {selectedIds.length}/4
          </span>
          {selectedIds.map(id => (
            <span
              key={id}
              className="inline-flex items-center gap-1.5 px-3 py-1 bg-gray-700 rounded-lg text-sm text-white shrink-0"
            >
              {potentials[id]?.display_name || potentials[id]?.name || id.slice(0, 8)}
              <button
                onClick={() => onRemove(id)}
                className="text-gray-400 hover:text-red-400 transition"
                aria-label={`移除 ${potentials[id]?.name || id}`}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <button
            onClick={onClear}
            className="px-3 py-1.5 text-sm text-gray-400 hover:text-white transition"
          >
            清空
          </button>
          <button
            onClick={handleCompare}
            disabled={!canCompare}
            className="px-5 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition"
          >
            {selectedIds.length < 2
              ? `还需选择 ${2 - selectedIds.length} 个`
              : `开始对比 (${selectedIds.length})`}
          </button>
        </div>
      </div>
    </div>
  )
}
