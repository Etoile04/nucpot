import { NextRequest, NextResponse } from 'next/server'
import { supabaseAdmin } from '@/lib/supabase'
import { verifyAdmin } from '@/lib/verify-admin'

// ── POST /api/admin/review ───────────────────────────────────────────────────
// Unified RPC proxy for all review operations
// Body: { action: string, params: Record<string, any> }
export async function POST(request: NextRequest) {
  if (!supabaseAdmin) {
    return NextResponse.json({ error: 'Admin client not configured' }, { status: 500 })
  }

  const admin = await verifyAdmin(request)
  if (!admin) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  let body: { action: string; params?: Record<string, unknown> }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const { action, params = {} } = body

  try {
    switch (action) {
      // ── Stats ────────────────────────────────────────────────────────────
      case 'stats': {
        const { data, error } = await supabaseAdmin.rpc('review_stats')
        if (error) throw error
        return NextResponse.json({ data })
      }

      // ── Queue params ─────────────────────────────────────────────────────
      case 'queue_params': {
        const { data, error } = await supabaseAdmin.rpc('review_queue_params', {
          p_status: params.status || null,
          p_value_type: params.value_type || null,
          p_material: params.material || null,
          p_source_file: params.source_file || null,
          p_limit: params.limit || 30,
          p_offset: params.offset || 0,
        })
        if (error) throw error
        const result = data && typeof data === 'object' && 'data' in (data as object)
          ? data
          : { data: [], total: 0 }
        return NextResponse.json({ data: result })
      }

      // ── Queue params with batch literature ───────────────────────────────
      case 'queue_params_with_lit': {
        const { data, error } = await supabaseAdmin.rpc('review_queue_params', {
          p_status: params.status || null,
          p_value_type: params.value_type || null,
          p_material: params.material || null,
          p_source_file: params.source_file || null,
          p_limit: params.limit || 30,
          p_offset: params.offset || 0,
        })
        if (error) throw error

        const result = data && typeof data === 'object' && 'data' in (data as object)
          ? (data as { data: any[]; total: number })
          : { data: [], total: 0 }

        // Batch fetch literature for all source_files in one pass
        const params_list = result.data || []
        const sourceFiles = [...new Set(params_list.map((p: any) => p.source_file).filter(Boolean))] as string[]
        const litMap = new Map<string, any[]>()

        if (sourceFiles.length > 0) {
          // Fetch literature for each unique source file (deduped)
          await Promise.all(
            sourceFiles.map(async (sf) => {
              try {
                const { data: litData } = await supabaseAdmin!.rpc('review_literature_for_source', {
                  p_source_file: sf,
                })
                litMap.set(sf, Array.isArray(litData) ? litData : [])
              } catch {
                litMap.set(sf, [])
              }
            })
          )
        }

        // Attach literature to each param
        const enriched = params_list.map((p: any) => ({
          ...p,
          literature: litMap.get(p.source_file) || [],
        }))

        return NextResponse.json({ data: { data: enriched, total: result.total || 0 } })
      }

      // ── Queue literature ─────────────────────────────────────────────────
      case 'queue_literature': {
        const { data, error } = await supabaseAdmin.rpc('review_queue_literature', {
          p_status: params.status || null,
          p_limit: params.limit || 30,
          p_offset: params.offset || 0,
        })
        if (error) throw error
        const result = data && typeof data === 'object' && 'data' in (data as object)
          ? data
          : { data: [], total: 0 }
        return NextResponse.json({ data: result })
      }

      // ── Literature for source ────────────────────────────────────────────
      case 'literature_for_source': {
        if (!params.source_file) {
          return NextResponse.json({ error: 'Missing source_file' }, { status: 400 })
        }
        const { data, error } = await supabaseAdmin.rpc('review_literature_for_source', {
          p_source_file: params.source_file as string,
        })
        if (error) throw error
        return NextResponse.json({ data: Array.isArray(data) ? data : [] })
      }

      // ── Batch update ─────────────────────────────────────────────────────
      case 'batch_update': {
        if (!Array.isArray(params.ids) || params.ids.length === 0) {
          return NextResponse.json({ error: 'Missing ids' }, { status: 400 })
        }
        const { data, error } = await supabaseAdmin.rpc('review_batch_update', {
          p_ids: params.ids,
          p_status: params.status,
          p_reviewer: admin.username || 'admin',
        })
        if (error) throw error
        return NextResponse.json({ data })
      }

      // ── Update single param ──────────────────────────────────────────────
      case 'update_param': {
        const { data, error } = await supabaseAdmin.rpc('review_update_param', {
          p_id: params.id,
          p_name: params.name || null,
          p_value_scalar: params.value_scalar != null ? Number(params.value_scalar) : null,
          p_value_min: params.value_min != null ? Number(params.value_min) : null,
          p_value_max: params.value_max != null ? Number(params.value_max) : null,
          p_value_expr: params.value_expr || null,
          p_value_text: params.value_text || null,
          p_unit: params.unit || null,
          p_confidence: params.confidence || null,
          p_notes: params.notes || null,
          p_review_status: params.review_status || 'approved',
          p_reviewer: admin.username || 'admin',
        })
        if (error) throw error
        return NextResponse.json({ data })
      }

      // ── Fix literature count ─────────────────────────────────────────────
      case 'fix_lit_count': {
        if (!params.id || params.actual_count == null) {
          return NextResponse.json({ error: 'Missing id or actual_count' }, { status: 400 })
        }
        const { error } = await supabaseAdmin
          .from('literature')
          .update({
            parameter_count: params.actual_count as number,
            review_status: 'approved',
            review_notes: `auto-fixed: ${params.old_count} -> ${params.actual_count}`,
            reviewed_at: new Date().toISOString(),
            reviewed_by: admin.username || 'admin',
          })
          .eq('id', params.id as string)
        if (error) throw error
        return NextResponse.json({ ok: true })
      }

      // ── Count params for literature ──────────────────────────────────────
      case 'count_params_for_lit': {
        if (!params.id) {
          return NextResponse.json({ error: 'Missing id' }, { status: 400 })
        }
        const { count, error } = await supabaseAdmin
          .from('parameters')
          .select('*', { count: 'exact', head: true })
          .or(`source_file.ilike.%${params.id}%,source_file.eq.${params.id}`)
        if (error) throw error
        return NextResponse.json({ count })
      }

      default:
        return NextResponse.json({ error: `Unknown action: ${action}` }, { status: 400 })
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err)
    console.error(`[review API] action=${action} error:`, message)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
