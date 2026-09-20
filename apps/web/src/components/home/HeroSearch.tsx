"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"

/**
 * Hero quick search (NFM-4990 首页五要素 · Hero).
 *
 * NFM-1064 §3.1 contract, retargeted to the P1 site map: Enter or the
 * button jumps to /potentials/search?q=… — the retired /search top-level
 * entry's job now lives on the secondary search route. §4.3: the search
 * button must not rely on Enter alone (touch).
 */
export function HeroSearch() {
  const router = useRouter()
  const [query, setQuery] = useState("")

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault()
        const q = query.trim()
        router.push(q ? `/potentials/search?q=${encodeURIComponent(q)}` : "/potentials/search")
      }}
      className="flex w-full max-w-xl items-center gap-2"
    >
      <label htmlFor="home-hero-search" className="sr-only">
        搜索势函数
      </label>
      <input
        id="home-hero-search"
        name="q"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="搜索材料 / 势函数…"
        className="flex-1 min-w-0 px-4 py-3 rounded-lg text-base text-gray-100 placeholder:text-gray-500 border focus:outline-none focus:border-[var(--form-input-focus-border,#3b82f6)]"
        style={{
          background: "var(--form-input-bg, #111827)",
          borderColor: "var(--form-input-border, #374151)",
        }}
      />
      <button
        type="submit"
        className="px-5 py-3 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-base font-medium transition-colors duration-150"
      >
        搜索
      </button>
    </form>
  )
}
