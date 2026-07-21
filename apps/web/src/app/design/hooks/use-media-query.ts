/**
 * useMediaQuery — subscribe to a CSS media query and re-render on change.
 *
 * Used by /design (NFM-1702) to switch the 280px left sidebar between
 * an inline panel (desktop) and a hamburger drawer (mobile <=768px).
 *
 * Returns the initial match synchronously, then updates on viewport changes.
 * Returns `false` when window.matchMedia is unavailable (SSR safety net).
 */

"use client"

import { useEffect, useState } from "react"

/**
 * Returns whether the given media query currently matches.
 *
 * @param query A valid CSS media query string (e.g. "(max-width: 768px)")
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(false)

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return
    }

    const list = window.matchMedia(query)
    setMatches(list.matches)

    const handler = (event: MediaQueryListEvent) => {
      setMatches(event.matches)
    }

    list.addEventListener("change", handler)
    return () => {
      list.removeEventListener("change", handler)
    }
  }, [query])

  return matches
}