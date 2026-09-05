import { NextResponse } from 'next/server'

const API_BASE = process.env.API_SERVER_URL || 'http://nucpot-prod-api:8000'

/**
 * BFF proxy for aggregate database statistics (element filter candidates).
 *
 * NFM-4310 (BUG-29): upstream failures must surface as errors here.
 * The previous implementation fabricated a 200 with empty stats on any
 * failure, which made the /browse and /search element filter render
 * 「无匹配元素」with no way to distinguish outage from empty library.
 */
export async function GET() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/stats`, {
      headers: { 'Content-Type': 'application/json' },
    })

    if (!res.ok) {
      return NextResponse.json(
        { success: false, error: `统计服务返回 ${res.status}` },
        { status: 502 }
      )
    }

    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(
      { success: false, error: '统计服务不可用' },
      { status: 502 }
    )
  }
}
