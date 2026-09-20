/**
 * Server-side data access for dataset detail (server components only).
 *
 * NFM-5020 (QA-FAILED AC-2 fix): /datasets/{id} is a server component,
 * so its fetch runs in Node where a relative URL throws
 * `Failed to parse URL from /api/datasets/{id}` and the page rendered
 * an error alert instead of the dataset. This module always resolves
 * an absolute base and calls the co-located FastAPI directly,
 * mirroring lib/blog/public-posts.ts (NFM-4940). The same-origin BFF
 * routes under /api/datasets stay the browser-side path — see
 * lib/datasets-api.ts (browser-only after this change).
 */
export interface DatasetDetail {
  id: string
  material_id: string
  source_id: string | null
  title: string
  description: string | null
  measurement_date: string | null
  is_verified: boolean
  created_at: string
  updated_at: string
  // NFM-4159 §5.2 attribution block — locked field names. Status is
  // 'placeholder' on recast-restored datasets, 'intact' otherwise.
  attribution: { status: "placeholder" | "intact" }
}

const REQUEST_TIMEOUT_MS = 15_000

/**
 * Absolute API base for server-side fetches: API_SERVER_URL first, then
 * the Docker-internal service DNS so SSR resolves inside any container.
 */
function apiBaseUrl(): string {
  return process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"
}

export async function getDatasetServer(
  id: string,
): Promise<DatasetDetail> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(
      `${apiBaseUrl()}/api/v1/datasets/${encodeURIComponent(id)}`,
      {
        headers: { Accept: "application/json" },
        signal: controller.signal,
        cache: "no-store",
      },
    )
    if (response.status === 404) {
      throw new Error("数据集不存在")
    }
    if (!response.ok) {
      throw new Error(
        `加载数据集失败: ${response.status} ${response.statusText}`.trim(),
      )
    }
    const envelope = (await response.json()) as {
      success?: boolean
      data?: DatasetDetail
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
