/**
 * Internal analytics ingestion endpoint (NFM-4181).
 *
 * Replaces the NFM-4146 console-stub destination: frontend events now land
 * in the `public.frontend_events` Supabase table (see
 * scripts/nfm-4181-frontend-events-table.sql) which powers the
 * disclosure-rate dashboards.
 *
 * Contract: POST { event, payload, ts } where `event` is allowlisted and
 * `payload` shape is part of the cross-product analytics contract
 * (spec NFM-4134.A §6.4). Invalid requests are rejected with 400; valid
 * ones return 204 so the client fire-and-forget path stays simple.
 *
 * Ticket: NFM-4181 (FU of NFM-4146).
 */

import { NextRequest, NextResponse } from "next/server"
import { supabase, supabaseAdmin } from "@/lib/supabase"

export const dynamic = "force-dynamic"

/** Cross-product analytics contract — event names are frozen. */
const ALLOWED_EVENTS: ReadonlySet<string> = new Set([
  "data_loss_notice.viewed",
  "data_loss_notice.dismissed",
  "data_loss_notice.learn_more_clicked",
])

/** Hard cap on the JSON payload size (bytes) — abuse guard. */
const MAX_PAYLOAD_BYTES = 4 * 1024
/** Hard cap on individual string fields inside the payload. */
const MAX_STRING_LENGTH = 256
/** Reject client timestamps further than 1h from server time. */
const MAX_TS_SKEW_MS = 60 * 60 * 1000

interface IncomingEvent {
  event?: unknown
  payload?: unknown
  ts?: unknown
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function validatePayload(
  payload: unknown,
): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  if (payload === undefined || payload === null) {
    return { ok: true, value: {} }
  }
  if (!isPlainObject(payload)) {
    return { ok: false, error: "payload must be an object" }
  }
  for (const [key, value] of Object.entries(payload)) {
    if (key.length > MAX_STRING_LENGTH) {
      return { ok: false, error: `payload key too long: ${key.slice(0, 32)}…` }
    }
    if (typeof value === "string" && value.length > MAX_STRING_LENGTH) {
      return { ok: false, error: `payload value too long for key: ${key}` }
    }
    if (
      typeof value !== "string" &&
      typeof value !== "number" &&
      typeof value !== "boolean" &&
      value !== null
    ) {
      return { ok: false, error: `payload value must be scalar, key: ${key}` }
    }
  }
  return { ok: true, value: payload }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  let body: IncomingEvent
  try {
    const raw = await request.text()
    if (raw.length > MAX_PAYLOAD_BYTES) {
      return NextResponse.json({ error: "body too large" }, { status: 400 })
    }
    body = JSON.parse(raw) as IncomingEvent
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 })
  }

  if (typeof body.event !== "string" || !ALLOWED_EVENTS.has(body.event)) {
    return NextResponse.json({ error: "unknown event" }, { status: 400 })
  }
  if (typeof body.ts !== "number" || !Number.isFinite(body.ts)) {
    return NextResponse.json({ error: "ts must be a number" }, { status: 400 })
  }
  const skew = Math.abs(Date.now() - body.ts)
  if (skew > MAX_TS_SKEW_MS) {
    return NextResponse.json({ error: "ts too far from server time" }, { status: 400 })
  }

  const validated = validatePayload(body.payload)
  if (!validated.ok) {
    return NextResponse.json({ error: validated.error }, { status: 400 })
  }

  const client = supabaseAdmin ?? supabase
  const { error } = await client.from("frontend_events").insert({
    event: body.event,
    payload: validated.value,
    client_ts: body.ts,
  })
  if (error) {
    // Ingestion failure is logged server-side and surfaced as a 500 so ops
    // dashboards catch warehouse gaps; the client swallows the error.
    console.error("[analytics] insert failed", body.event, error.message)
    return NextResponse.json({ error: "ingestion failed" }, { status: 500 })
  }

  return new NextResponse(null, { status: 204 })
}

export function GET(): NextResponse {
  return NextResponse.json({ error: "method not allowed" }, { status: 405 })
}
