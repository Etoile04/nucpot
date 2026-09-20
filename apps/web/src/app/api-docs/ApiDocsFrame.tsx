"use client"

// NFM-4991: client-side iframe wrapper for the FastAPI Swagger UI proxy.
// Kept as a client component so the iframe can resize responsively to the
// remaining viewport (header is rendered by the server-component page).
// `loading="lazy"` is irrelevant for the initial paint but signals intent
// to screen readers; the title attribute provides the accessible name.

import { useEffect, useRef, useState } from "react"

export function ApiDocsFrame() {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number>(600)

  useEffect(() => {
    if (typeof window === "undefined") return
    const update = () => {
      const top = wrapperRef.current?.getBoundingClientRect().top ?? 0
      // Leave 24px breathing room at the bottom so the iframe doesn't
      // crash into the viewport edge on browsers that draw native
      // scrollbars inside the embedded document.
      const remaining = Math.max(320, window.innerHeight - top - 24)
      setHeight(remaining)
    }
    update()
    window.addEventListener("resize", update)
    return () => window.removeEventListener("resize", update)
  }, [])

  return (
    <div ref={wrapperRef} className="flex-1 w-full bg-gray-900">
      <iframe
        title="NFMD API Swagger 文档"
        src="/api-docs/swagger"
        width="100%"
        height={height}
        className="block w-full border-0"
        loading="lazy"
      />
    </div>
  )
}