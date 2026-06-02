// Client-side wrapper for review API calls (routes through authenticated API route)
// This avoids exposing service_role key to the browser

import type { Session } from '@supabase/supabase-js'

export function reviewApi(session: Session | null) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  return {
    async post(action: string, params: Record<string, unknown> = {}) {
      const res = await fetch('/api/admin/review', {
        method: 'POST',
        headers,
        body: JSON.stringify({ action, params }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || `Request failed (${res.status})`)
      return json
    },

    // Convenience helpers
    stats: () => reviewApi(session).post('stats'),

    queueParams: (opts: {
      status?: string; value_type?: string; material?: string;
      source_file?: string; limit?: number; offset?: number
    }) => reviewApi(session).post('queue_params_with_lit', opts),

    queueLiterature: (opts: {
      status?: string | null; limit?: number; offset?: number
    }) => reviewApi(session).post('queue_literature', opts),

    literatureForSource: (sourceFile: string) =>
      reviewApi(session).post('literature_for_source', { source_file: sourceFile }),

    batchUpdate: (ids: string[], status: string, reviewer?: string) =>
      reviewApi(session).post('batch_update', { ids, status, reviewer }),

    updateParam: (params: Record<string, unknown>) =>
      reviewApi(session).post('update_param', params),

    fixLitCount: (id: string, actualCount: number, oldCount: number) =>
      reviewApi(session).post('fix_lit_count', { id, actual_count: actualCount, old_count: oldCount }),

    countParamsForLit: (id: string) =>
      reviewApi(session).post('count_params_for_lit', { id }),
  }
}
