import type { Metadata } from "next"
import Link from "next/link"
import { cache } from "react"
import { Alert } from "antd"
import {
  getDatasetServer,
  type DatasetDetail,
} from "@/lib/datasets-server"
import { DatasetDetailContent } from "./DatasetDetailContent"

interface PageProps {
  params: Promise<{ id: string }>
}

// NFM-5321 (Visual-Truth Gate retro-gate D1/D2/D4): metadata renders the
// dataset's real title (not a truncated UUID), the description is
// user-facing copy with no internal ticket ids, and the fetch resolves
// material/source names so no raw UUIDs reach the page. `cache` dedupes
// the no-store fetch across generateMetadata and the page render; the
// outcome shape keeps the API's specific error message (e.g. 404 →
// 「数据集不存在」) available to the page body.
//
// NFM-5321 hotfix (deploy of d4c33eb6a): the antd-rendering subtree
// moved into DatasetDetailContent ("use client") — antd Typography
// statics (.Title/.Text) resolve to undefined in the server-component
// runtime and SSR-crashed every detail request with HTTP 500. This page
// stays a Server Component for the cached fetch + metadata; see
// DatasetDetailContent.tsx for the root-cause notes.
type DatasetOutcome = { dataset: DatasetDetail; error: null } | { dataset: null; error: string }

const fetchDatasetOutcome = cache(
  async (id: string): Promise<DatasetOutcome> => {
    // Server component → Node fetch: the server module resolves an
    // absolute API base (NFM-5020) and unwraps the envelope; errors
    // resolve to the error arm so generateMetadata can fall back to a
    // generic title instead of throwing during <head> generation.
    try {
      return { dataset: await getDatasetServer(id, { expand: "material,source" }), error: null }
    } catch (err) {
      return {
        dataset: null,
        error: err instanceof Error ? err.message : "数据集加载失败",
      }
    }
  },
)

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { id } = await params
  const { dataset } = await fetchDatasetOutcome(id)
  return {
    title: dataset ? `${dataset.title} - NucPot` : "数据集详情 - NucPot",
    description: dataset
      ? `查看数据集「${dataset.title}」的测量材料、数据来源、测量日期与数据归属状态。`
      : "查看核材料数据集详情：测量材料、数据来源、测量日期与数据归属状态。",
  }
}

export default async function DatasetDetailPage({ params }: PageProps) {
  const { id } = await params
  const { dataset, error: errorMessage } = await fetchDatasetOutcome(id)

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <nav className="text-sm mb-4">
        <Link href="/datasets" className="text-blue-400 hover:text-blue-300 hover:underline">
          ← 返回数据集列表
        </Link>
      </nav>

      {errorMessage ? (
        <Alert
          type="error"
          showIcon
          message="数据集加载失败"
          description={errorMessage}
        />
      ) : null}

      {dataset ? <DatasetDetailContent dataset={dataset} /> : null}
    </main>
  )
}
