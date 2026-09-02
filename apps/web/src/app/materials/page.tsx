import type { Metadata } from "next"
import { Suspense } from "react"
import { Skeleton } from "antd"
import { MaterialsListView } from "./MaterialsListView"

export const metadata: Metadata = {
  title: "材料列表 - NFMD",
  description: "浏览核燃料与材料数据库中的全部材料",
}

/**
 * NFM-3917 / Tier 1D: ``MaterialsListView`` is a client component that
 * reads ``?category_id=`` via ``useSearchParams``. Next.js 16 prerender
 * requires any such component to sit inside a ``<Suspense>`` boundary
 * at the page level, otherwise static export fails with
 * ``missing-suspense-with-csr-bailout``. The fallback is a Skeleton
 * sized to match the eventual filter bar + table layout.
 */
export default function MaterialsListPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-[1200px] mx-auto px-6 py-8">
          <Skeleton active paragraph={{ rows: 6 }} />
        </div>
      }
    >
      <MaterialsListView />
    </Suspense>
  )
}