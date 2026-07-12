/**
 * useReducedMotion — detects the user's prefers-reduced-motion setting.
 *
 * Returns true if the user has requested reduced motion via their
 * OS accessibility settings. Used by GraphCanvas to skip force layout
 * animations and use static positions instead.
 */

import { useState, useEffect } from "react"

const QUERY = "(prefers-reduced-motion: reduce)"

export function useReducedMotion(): boolean {
  const [prefersReduced, setPrefersReduced] = useState(false)

  useEffect(() => {
    const mql = window.matchMedia(QUERY)
    setPrefersReduced(mql.matches)

    const handler = (event: MediaQueryListEvent) => {
      setPrefersReduced(event.matches)
    }

    mql.addEventListener("change", handler)
    return () => {
      mql.removeEventListener("change", handler)
    }
  }, [])

  return prefersReduced
}
