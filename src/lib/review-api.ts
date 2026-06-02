import type { Session } from '@supabase/supabase-js'

/**
 * Client-side helper for the /api/admin/review unified RPC proxy.
 * All admin operations go through this API route with JWT auth.
 *
 * Usage:
 *   const api = reviewApi(session)
 *   const { data } = await api.stats()
 *   const { data } = await api.queueParams({ status: 'needs_review', limit: 30, offset: 0 })
 */

interface ApiParams {
  status?: string
  value_type?: string
  material?: string
  source_file?: string
  limit?: number
  offset?: number
  [key: string]: unknown
}

class ReviewApiClient {
  private token: string | undefined

  constructor(session: Session | null) {
    this.token = session?.access_token
  }

  private async post(action: string, params: Record<string, unknown> = {}) {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.token) headers['Authorization'] = `Bearer ${this.token}`

    const res = await fetch('/api/admin/review', {
      method: 'POST',
      headers,
      body: JSON.stringify({ action, params }),
    })

    const data = await res.json()
    if (!res.ok) throw new Error(data.error || `API error: ${res.status}`)
    return { data, status: res.status }
  }

  stats() {
    return this.post('stats')
  }

  queueParams(params: ApiParams) {
    return this.post('queue_params_with_lit', params)
  }

  queueLiterature(params: ApiParams) {
    return this.post('queue_literature', params)
  }

  literatureForSource(sourceFile: string) {
    return this.post('literature_for_source', { source_file: sourceFile })
  }

  batchUpdate(ids: string[], status: string, reviewer?: string) {
    return this.post('batch_update', { ids, status, reviewer })
  }

  updateParam(params: Record<string, unknown>) {
    return this.post('update_param', params)
  }

  fixLitCount(id: string, actualCount: number, oldCount: number) {
    return this.post('fix_lit_count', { id, actual_count: actualCount, old_count: oldCount })
  }

  countParamsForLit(litId: string) {
    return this.post('count_params_for_lit', { id: litId })
  }
}

/**
 * Create a ReviewApiClient instance. Call once and reuse within a component.
 */
export function reviewApi(session: Session | null): ReviewApiClient {
  return new ReviewApiClient(session)
}
