import { NextResponse } from "next/server";
import { supabase, supabaseAdmin } from "@/lib/supabase";

/**
 * GET /api/potentials/[id] — return full detail for a single potential.
 *
 * Uses supabaseAdmin (service-role key, bypasses RLS) when available,
 * falling back to the anon supabase client. This fixes NFM-2536 where
 * RLS policies on the potentials table blocked anon single-row reads
 * while still allowing range/list queries.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  const client = supabaseAdmin ?? supabase;

  const { data, error } = await client
    .from("potentials")
    .select("*")
    .eq("id", id)
    .eq("status", "published")
    .single();

  if (error || !data) {
    const detail = error
      ? `Supabase error ${error.code}: ${error.message}`
      : "No data returned";
    return NextResponse.json(
      { error: "Potential not found", detail },
      { status: 404 },
    );
  }

  return NextResponse.json(data);
}
