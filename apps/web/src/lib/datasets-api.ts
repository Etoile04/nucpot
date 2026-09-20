/**
 * Browser-side API client for dataset endpoints.
 *
 * NFM-4991 (IA-REFACTOR P2 /datasets block):
 *   - listDatasets() → GET /api/datasets          (BFF → FastAPI)
 *
 * Same-origin BFF routes so client components can hit `/api/datasets`
 * without worrying about the FastAPI host or CORS. The BFF forwards to
 * the co-located FastAPI (`API_SERVER_URL`) and unwraps the standard
 * `{success, data}` envelope.
 *
 * BROWSER-ONLY (NFM-5020): these relative URLs throw in Node
 * (`Failed to parse URL from /api/...`) when fetched from a server
 * component. Server-side data access lives in lib/datasets-server.ts,
 * which resolves an absolute API base — the former getDataset() moved
 * there as getDatasetServer() after it broke /datasets/{id} SSR.
 */

export interface DatasetListItem {
  id: string
  material_id: string
  material_name: string | null
  source_id: string | null
  source_title: string | null
  title: string
  measurement_date: string | null
  is_verified: boolean
  created_at: string
  updated_at: string
}

export interface DatasetListResult {
  items: DatasetListItem[]
  total: number
  page: number
  limit: number
  pages: number
  truncated: boolean
}

export interface DatasetListParams {
  page?: number
  perPage?: number
  materialId?: string
  sourceId?: string
  isVerified?: boolean
  expand?: "material" | "source" | "material,source"
}

const REQUEST_TIMEOUT_MS = 15_000

export async function listDatasets(
  params: DatasetListParams = {},
): Promise<DatasetListResult> {
  const sp = new URLSearchParams()
  if (params.page) sp.set("page", String(params.page))
  if (params.perPage) sp.set("per_page", String(params.perPage))
  if (params.materialId) sp.set("material_id", params.materialId)
  if (params.sourceId) sp.set("source_id", params.sourceId)
  if (params.isVerified !== undefined) {
    sp.set("is_verified", String(params.isVerified))
  }
  if (params.expand) sp.set("expand", params.expand)

  const qs = sp.toString()
  const url = qs ? `/api/datasets?${qs}` : "/api/datasets"
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      cache: "no-store",
    })
    if (!response.ok) {
      throw new Error(
        `加载数据集失败: ${response.status} ${response.statusText}`.trim(),
      )
    }
    const envelope = (await response.json()) as {
      success?: boolean
      data?: DatasetListResult
      error?: string
    }
    if (!envelope.success || !envelope.data) {
      throw new Error(envelope.error ?? "数据集响应格式异常")
    }
    return envelope.data
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error("数据集请求超时")
    }
    throw err
  } finally {
    clearTimeout(timeout)
  }
}