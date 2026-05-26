import { NextRequest, NextResponse } from 'next/server'

const AUTOCV_API_URL = process.env.NEXT_PUBLIC_AUTOCV_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  const url = `${AUTOCV_API_URL}/api/verification/v2`
  try {
    const body = await request.text()
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    const resBody = await upstream.text()
    return new NextResponse(resBody, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (error) {
    return NextResponse.json({ error: 'Verification service unavailable' }, { status: 502 })
  }
}
