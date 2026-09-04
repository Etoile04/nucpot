"use client"

/**
 * G3-S5 (NFM-4093): literature extraction-gap count badge.
 *
 * Shows how many open extraction gaps a literature record produced
 * (per the ontology-driven gap scan wired in NFM-4077). Data comes
 * from the detail endpoint's `gap_count` field when present; the
 * verify/literature tables render it next to the verification badge so
 * reviewers can see "this paper is missing N expected properties".
 */

import { useEffect, useState } from "react"

export default function GapCountBadge({
  literatureId,
}: {
  literatureId: string
}) {
  const [count, setCount] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`/api/v1/literature/${literatureId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return
        const n = d?.data?.gap_count
        setCount(typeof n === "number" ? n : 0)
      })
      .catch(() => {
        if (!cancelled) setCount(0)
      })
    return () => {
      cancelled = true
    }
  }, [literatureId])

  if (count === null) return null
  if (count === 0) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-green-900/40 text-green-300 border border-green-700">
        无缺口
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-amber-900/40 text-amber-300 border border-amber-700"
      title={`${count} 个本体期望属性未在本文献中找到`}
    >
      缺口 {count}
    </span>
  )
}
