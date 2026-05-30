'use client'

import { useEffect, useRef, useState, useMemo, useCallback } from 'react'

interface SuggestionItem {
  id: string
  display_name: string
  name: string
  system_name: string
  elements: string[]
  type: string
}

interface SearchSuggestionsProps {
  query: string
  onSelect: (value: string) => void
  onOpenChange?: (open: boolean) => void
}

function buildMatchText(item: SuggestionItem): string[] {
  return [
    item.display_name,
    item.name,
    item.system_name,
    item.elements.join('-'),
    item.type,
  ]
}

export default function SearchSuggestions({ query, onSelect, onOpenChange }: SearchSuggestionsProps) {
  const [allPotentials, setAllPotentials] = useState<SuggestionItem[]>([])
  const [highlightIndex, setHighlightIndex] = useState(-1)
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // Fetch all potentials once
  useEffect(() => {
    fetch('/api/potentials?limit=1000')
      .then(r => r.json())
      .then(data => setAllPotentials(data.potentials || []))
      .catch(() => {})
  }, [])

  // Filter suggestions
  const suggestions = useMemo(() => {
    if (query.length < 2) return []
    const q = query.toLowerCase()
    return allPotentials
      .filter(item => {
        const fields = buildMatchText(item)
        return fields.some(f => f && f.toLowerCase().includes(q))
      })
      .slice(0, 8)
  }, [query, allPotentials])

  // Reset highlight when suggestions change
  useEffect(() => {
    setHighlightIndex(-1)
  }, [query])

  // Open/close based on query length
  useEffect(() => {
    const shouldOpen = query.length >= 2 && suggestions.length > 0
    if (shouldOpen !== isOpen) {
      setIsOpen(shouldOpen)
      onOpenChange?.(shouldOpen)
    }
  }, [query, suggestions.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close on click outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
        onOpenChange?.(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onOpenChange])

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlightIndex >= 0 && listRef.current) {
      const el = listRef.current.children[highlightIndex] as HTMLElement | undefined
      el?.scrollIntoView({ block: 'nearest' })
    }
  }, [highlightIndex])

  const handleSelect = useCallback((item: SuggestionItem) => {
    onSelect(item.display_name || item.name)
    setIsOpen(false)
    onOpenChange?.(false)
  }, [onSelect, onOpenChange])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!isOpen || suggestions.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIndex(prev => (prev + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIndex(prev => (prev - 1 + suggestions.length) % suggestions.length)
    } else if (e.key === 'Enter') {
      if (highlightIndex >= 0 && highlightIndex < suggestions.length) {
        e.preventDefault()
        handleSelect(suggestions[highlightIndex])
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
      onOpenChange?.(false)
    }
  }, [isOpen, suggestions, highlightIndex, handleSelect, onOpenChange])

  // Highlight matched text
  function highlight(text: string, q: string) {
    const idx = text.toLowerCase().indexOf(q.toLowerCase())
    if (idx === -1) return text
    return (
      <>
        {text.slice(0, idx)}
        <span className="text-yellow-300 font-medium">{text.slice(idx, idx + q.length)}</span>
        {text.slice(idx + q.length)}
      </>
    )
  }

  if (!isOpen) {
    return <div ref={containerRef} onKeyDown={handleKeyDown} />
  }

  return (
    <div ref={containerRef} className="relative">
      <ul
        ref={listRef}
        className="absolute z-50 top-0 left-0 right-0 mt-0.5 bg-gray-800 border border-gray-600 rounded-lg shadow-xl max-h-80 overflow-y-auto"
        role="listbox"
      >
        {suggestions.map((item, idx) => (
          <li
            key={item.id}
            role="option"
            aria-selected={idx === highlightIndex}
            className={`px-4 py-2.5 cursor-pointer text-sm transition ${
              idx === highlightIndex
                ? 'bg-blue-600/30 text-white'
                : 'text-gray-300 hover:bg-gray-700/60 hover:text-white'
            } ${idx > 0 ? 'border-t border-gray-700/50' : ''}`}
            onMouseEnter={() => setHighlightIndex(idx)}
            onClick={() => handleSelect(item)}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium truncate">
                {highlight(item.display_name || item.name, query)}
              </span>
              <span className="flex items-center gap-1.5 shrink-0 text-xs text-gray-500">
                <span className="px-1.5 py-0.5 bg-gray-700 rounded text-gray-400">{item.type}</span>
                <span className="px-1.5 py-0.5 bg-gray-700 rounded text-gray-400">
                  {item.elements.join('-')}
                </span>
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Export the keydown handler so the parent input can delegate
export { handleSuggestionsKeyDown as delegateKeyDown }

/** Call this from the parent input's onKeyDown after your own logic. Returns true if the event was handled. */
function handleSuggestionsKeyDown(
  e: React.KeyboardEvent,
  isOpen: boolean,
  suggestionsCount: number,
  highlightIndex: number,
  moveDown: () => void,
  moveUp: () => void,
  confirm: () => void,
  close: () => void,
): boolean {
  if (!isOpen || suggestionsCount === 0) return false
  if (e.key === 'ArrowDown') { e.preventDefault(); moveDown(); return true }
  if (e.key === 'ArrowUp') { e.preventDefault(); moveUp(); return true }
  if (e.key === 'Enter' && highlightIndex >= 0) { e.preventDefault(); confirm(); return true }
  if (e.key === 'Escape') { close(); return true }
  return false
}
