import { Suspense } from "react"
import { Spin } from "antd"
import { KgExploreContent } from "./KgExploreContent"

export default function KgExplorePage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center items-center min-h-[400px]">
          <Spin tip="Loading…" />
        </div>
      }
    >
      <KgExploreContent />
    </Suspense>
  )
}
