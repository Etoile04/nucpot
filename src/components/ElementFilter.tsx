'use client'

import { useState } from 'react'

interface ElementFilterProps {
  allElements: string[]
  selected: string[]
  onToggle: (element: string) => void
}

export default function ElementFilter({ allElements, selected, onToggle }: ElementFilterProps) {
  const [search, setSearch] = useState('')

  const filtered = search
    ? allElements.filter(e => e.toLowerCase().includes(search.toLowerCase()))
    : allElements

  return (
    <div>
      <input
        type="text"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="搜索元素..."
        className="w-full px-2 py-1.5 mb-2 rounded bg-gray-700 border border-gray-600 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
        aria-label="搜索元素"
      />
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="元素选择">
        {filtered.map(el => (
          <button
            key={el}
            onClick={() => onToggle(el)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium border transition ${
              selected.includes(el)
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'bg-gray-700 border-gray-600 text-gray-300 hover:border-blue-500/50'
            }`}
            aria-pressed={selected.includes(el)}
          >
            {el}
          </button>
        ))}
        {filtered.length === 0 && (
          <span className="text-xs text-gray-500">无匹配元素</span>
        )}
      </div>
    </div>
  )
}
