"use client"

import { Suspense } from "react"
import { Spin } from "antd"
import { KgSearchContent } from "./KgSearchContent"

export default function KgSearchPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center items-center min-h-[400px]">
          <Spin tip="Loading…" />
        </div>
      }
    >
      <KgSearchContent />
    </Suspense>
  )
}
