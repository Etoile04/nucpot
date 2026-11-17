import type { Metadata } from "next"
import { Suspense } from "react"
import { Skeleton } from "antd"
import { MaterialsListView } from "./MaterialsListView"

export const metadata: Metadata = {
  title: "材料列表 - NFMD",
  description: "浏览核燃料与材料数据库中的全部材料",
}

// NFM-5418: without this, a fully-static page emits Next.js's default
// s-maxage=31536000 HTML header, so the Cloudflare edge pins it for a year
// and any deploy that changes chunk hashes orphans the cached refs
// (NFM-5335 class). NFM-5863 widened the window to 30 min: the emitted
// s-maxage must sit above the site-monitor probe cadence (~320-400s
// observed) so edge probes never land on the cold-fill path (5.3-9.4s P1
// singles, NFM-5862), while staying below the NFM-5418 stale-pin lane
// ceiling of 3600s. Match the home page's ISR window: the shell
// revalidates every 30 min behind stale-while-revalidate.
export const revalidate = 1800

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
