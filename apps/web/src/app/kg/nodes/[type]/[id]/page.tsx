/**
 * KG Node Detail route entry (NFM-1337).
 *
 * Dynamic route at /kg/nodes/[type]/[id]. The actual rendering lives in
 * the client component `KgNodeDetailContent`, which fetches the node
 * from GET /api/v1/kg/nodes/{type}/{id}. This file just unpacks the
 * dynamic route params and wires Suspense with a content-shape
 * Skeleton fallback so the initial server response includes a stable
 * loading shell for fast LCP (no layout shift when the real content
 * arrives).
 */

import { Suspense } from "react"
import { Skeleton } from "antd"
import { KgNodeDetailContent } from "./KgNodeDetailContent"

interface KgNodeDetailPageProps {
  readonly params: Promise<{
    readonly type: string
    readonly id: string
  }>
}

export default async function KgNodeDetailPage({
  params,
}: KgNodeDetailPageProps) {
  const { type, id } = await params
  return (
    <Suspense
      fallback={
        <main
          role="status"
          aria-busy="true"
          aria-label="Loading knowledge graph node"
          data-testid="kg-node-detail-loading"
          className="max-w-[1200px] mx-auto px-6 py-8"
        >
          <Skeleton.Button
            active
            size="small"
            className="!w-16 !h-6 !rounded mb-6"
            aria-hidden
          />
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <Skeleton.Input
              active
              size="small"
              className="!w-20 !h-6 !rounded"
              aria-hidden
            />
            <Skeleton.Input
              active
              size="small"
              className="!w-24 !h-6 !rounded"
              aria-hidden
            />
            <Skeleton.Input
              active
              size="small"
              className="!w-16 !h-6 !rounded"
              aria-hidden
            />
          </div>
          <Skeleton.Input
            active
            size="large"
            className="!w-2/3 !h-10 !rounded mb-2"
            aria-hidden
          />
          <Skeleton.Input
            active
            size="small"
            className="!w-1/3 !h-4 !rounded mb-10"
            aria-hidden
          />
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8">
            <div className="space-y-6">
              <Skeleton.Input
                active
                size="small"
                className="!w-32 !h-6 !rounded"
                aria-hidden
              />
              <Skeleton
                active
                paragraph={{ rows: 4, width: ["60%", "80%", "55%", "70%"] }}
                title={false}
              />
            </div>
            <aside className="space-y-4">
              <Skeleton.Input
                active
                size="small"
                className="!w-28 !h-6 !rounded"
                aria-hidden
              />
              <Skeleton
                active
                paragraph={{ rows: 3, width: ["85%", "90%", "60%"] }}
                title={false}
              />
            </aside>
          </div>
        </main>
      }
    >
      <KgNodeDetailContent type={type} id={id} />
    </Suspense>
  )
}
