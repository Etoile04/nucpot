import { NextResponse } from "next/server"

/**
 * BFF route for /api/datasets/{id} — single dataset with §5.2
 * attribution block (NFM-4159).
 *
 * Thin wrapper around the FastAPI endpoint. The BFF does NOT mutate
 * the attribution block — it's locked per NFM-4159 §5.2, and the
 * frontend asserts the negative in its regression tests (§7c).
 */

const BACKEND_TIMEOUT_MS = 8_000

function backendBase(): string {
  return process.env.API_SERVER_URL ?? "http://localhost:8001"
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await context.params
  // Guard against empty / malformed IDs hitting the backend.
  if (!/^[0-9a-fA-F-]{36}$/.test(id)) {
    return NextResponse.json(
      { success: false, error: "数据集 ID 格式不正确" },
      { status: 400 },
    )
  }
  const target = `${backendBase()}/api/v1/datasets/${id}`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), BACKEND_TIMEOUT_MS)
  try {
    const response = await fetch(target, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      cache: "no-store",
    })
    if (response.status === 404) {
      return NextResponse.json(
        { success: false, error: "数据集不存在" },
        { status: 404 },
      )
    }
    if (!response.ok) {
      return NextResponse.json(
        {
          success: false,
          error: `后端返回 ${response.status} ${response.statusText || ""}`.trim(),
        },
        { status: response.status },
      )
    }
    const envelope = (await response.json()) as {
      success?: boolean
      data?: unknown
      error?: string
    }
    if (!envelope.success || !envelope.data) {
      return NextResponse.json(
        { success: false, error: envelope.error ?? "后端响应格式异常" },
        { status: 502 },
      )
    }
    return NextResponse.json({ success: true, data: envelope.data })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json(
      { success: false, error: `后端不可达: ${message}` },
      { status: 502 },
    )
  } finally {
    clearTimeout(timeout)
  }
}