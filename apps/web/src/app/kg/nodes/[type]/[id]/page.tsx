/**
 * KG Node Detail route entry (NFM-1337).
 *
 * Dynamic route at /kg/nodes/[type]/[id]. The actual rendering lives in
 * the client component `KgNodeDetailContent`, which fetches the node
 * from GET /api/v1/kg/nodes/{type}/{id}. This file just unpacks the
 * dynamic route params and wires Suspense with a Spin fallback so the
 * initial server response includes a loading shell for fast LCP.
 */

import { Suspense } from "react"
import { Spin } from "antd"
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
        <div
          role="status"
          aria-label="Loading knowledge graph node"
          className="flex justify-center items-center min-h-[400px]"
        >
          <Spin tip="Loading node…" />
        </div>
      }
    >
      <KgNodeDetailContent type={type} id={id} />
    </Suspense>
  )
}
