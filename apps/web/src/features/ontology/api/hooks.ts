/**
 * TanStack Query hooks for ontology management.
 * Endpoints from apps/api/src/nfm_db/api/v1/ontology_version.py.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { request } from "@/lib/api-client"
import type {
  OntologyVersion,
  PaginatedResponse,
  OntologyVersionListParams,
  CreateDraftPayload,
  PublishPayload,
} from "./types"

const BASE = "/api/v1/ontology/versions"

/* ── Query key factory ─────────────────────────────────────── */

export const ontologyKeys = {
  all: ["ontology", "versions"] as const,
  lists: () => [...ontologyKeys.all, "list"] as const,
  list: (params: OntologyVersionListParams) =>
    [...ontologyKeys.lists(), params] as const,
  details: () => [...ontologyKeys.all, "detail"] as const,
  detail: (id: string) => [...ontologyKeys.details(), id] as const,
} as const

/* ── Read hooks ────────────────────────────────────────────── */

/**
 * GET /api/v1/ontology/versions — paginated list with optional status filter.
 */
export function useOntologyVersions(params: OntologyVersionListParams = {}) {
  return useQuery({
    queryKey: ontologyKeys.list(params),
    queryFn: async (): Promise<PaginatedResponse<OntologyVersion>> => {
      const qs = new URLSearchParams()
      if (params.page) qs.set("page", String(params.page))
      if (params.limit) qs.set("limit", String(params.limit))
      if (params.status && params.status !== "all") {
        qs.set("status", params.status)
      }
      const q = qs.toString()
      const url = `${BASE}${q ? `?${q}` : ""}`
      return request<PaginatedResponse<OntologyVersion>>(url)
    },
    placeholderData: (prev) => prev,
  })
}

/**
 * GET /api/v1/ontology/versions/{id} — single version detail.
 */
export function useOntologyVersion(id: string) {
  return useQuery({
    queryKey: ontologyKeys.detail(id),
    queryFn: async (): Promise<OntologyVersion> => {
      const env = await request<{ success: boolean; data: OntologyVersion }>(
        `${BASE}/${id}`,
      )
      return env.data
    },
    enabled: id.length > 0,
  })
}

/* ── Mutation hooks ───────────────────────────────────────── */

/**
 * POST /api/v1/ontology/versions — create a new draft.
 */
export function useCreateDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: CreateDraftPayload): Promise<OntologyVersion> => {
      return request<OntologyVersion>(BASE, {
        method: "POST",
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ontologyKeys.lists() })
    },
  })
}

/**
 * POST /api/v1/ontology/versions/{id}/publish — publish a draft.
 */
export function usePublishVersion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: PublishPayload }): Promise<OntologyVersion> => {
      return request<OntologyVersion>(`${BASE}/${id}/publish`, {
        method: "POST",
        body: JSON.stringify(payload),
      })
    },
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ontologyKeys.lists() })
      void qc.invalidateQueries({ queryKey: ontologyKeys.detail(vars.id) })
    },
  })
}

/**
 * POST /api/v1/ontology/versions/{id}/deprecate — deprecate a published version.
 */
export function useDeprecateVersion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string): Promise<OntologyVersion> => {
      return request<OntologyVersion>(`${BASE}/${id}/deprecate`, {
        method: "POST",
      })
    },
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ontologyKeys.lists() })
      void qc.invalidateQueries({ queryKey: ontologyKeys.detail(id) })
    },
  })
}
