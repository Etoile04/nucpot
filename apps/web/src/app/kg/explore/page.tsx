/**
 * KG Explorer page — server component that fetches the full graph.
 *
 * Fetches GET /api/v1/kg/graph?limit=100 server-side and passes
 * the mapped GraphData to the client-side KgExploreView.
 *
 * Route: /kg/explore
 * Spec: NFM-1336
 */

import type { Metadata } from "next"
import { KgExploreView } from "./KgExploreView"
import { fetchFullGraphData } from "@/lib/kg-graph-api"

export const metadata: Metadata = {
  title: "知识图谱探索 - NFMD",
  description: "探索核材料知识图谱，浏览材料、属性与实体关系",
}

export default async function KgExplorePage() {
  const graphData = await fetchFullGraphData(100).catch(() => ({
    nodes: [],
    edges: [],
  }))

  return <KgExploreView initialData={graphData} />
}
