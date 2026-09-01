/**
 * Materials API client for material property endpoints.
 *
 * Uses the shared `request()` helper from api-client for JWT auth.
 *
 * Spec: NFM-1066 §1
 */

import { request } from "@/lib/api-client";
import type { ApiResponse } from "@/lib/api-client";
import type {
  GraphData,
  GraphEdge,
  GraphNode,
  GraphNodeType,
} from "@/components/graph/types";

// ── Types ──────────────────────────────────────────────────────────────

/**
 * Structured citation for a property measurement's data source.
 *
 * NFM-4086 — D1 来源可读化. The backend (apps/api/src/nfm_db/schemas/
 * property.py::SourceRef) returns this shape so the citation column can
 * render an interactive "Authors (Year). Journal." cell with a DOI link
 * instead of the legacy bare-title string.
 *
 *   - `authors`  Collapsed to ≤3 names + "et al." (see backend
 *                `_format_authors`).
 *   - `url`      Resolved by the backend: DOI resolver first, then the
 *                source's external_url. May still be null when neither
 *                identifier exists.
 */
export interface SourceRef {
  readonly id: string;
  readonly title: string;
  readonly doi: string | null;
  readonly journal: string | null;
  readonly year: number | null;
  readonly authors: ReadonlyArray<string>;
  readonly url: string | null;
}

export interface MaterialProperty {
  readonly id: string;
  readonly name: string;
  readonly value: string;
  readonly unit: string | null;
  readonly source: SourceRef | null;
  readonly confidence: number;
}

export interface MaterialPropertyMeta {
  readonly total: number;
  readonly page: number;
  readonly limit: number;
}

export interface MaterialPropertyListResponse {
  readonly data: ReadonlyArray<MaterialProperty>;
  readonly meta: MaterialPropertyMeta;
}

export interface MaterialPropertyListParams {
  readonly page?: number;
  readonly limit?: number;
  readonly sort?: string;
  readonly order?: "asc" | "desc";
  readonly filter?: string;
}

export interface MaterialSummary {
  readonly id: string;
  readonly name: string;
  readonly formula: string | null;
}

// ── Material categories (NFM-3917 / Tier 1D) ───────────────────────────

export interface MaterialCategory {
  readonly id: string;
  readonly name: string;
  readonly slug: string;
  readonly description: string | null;
  readonly parent_id: string | null;
  readonly sort_order: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface MaterialCategoryListEnvelope {
  readonly items: ReadonlyArray<MaterialCategory>;
}

/**
 * Fetch the full material-category taxonomy for the /materials filter
 * dropdown (NFM-3917 / Tier 1D).
 *
 * Returns the array directly (the ``ApiResponse`` envelope is unwrapped
 * here, mirroring `getMaterialProperties` / `getMaterial`). Returns an
 * empty array on a network error so the UI degrades to "no categories
 * available" instead of an error page; the caller surfaces a friendly
 * notice via the Select's ``notFoundContent``.
 */
export async function listMaterialCategories(): Promise<
  ReadonlyArray<MaterialCategory>
> {
  try {
    const envelope = await request<ApiResponse<MaterialCategoryListEnvelope>>(
      "/api/v1/material-categories",
    );
    return envelope.data.items;
  } catch {
    return [];
  }
}

// ── Uncategorized count (NFM-4030 / Tier 1D follow-up) ────────────────
//
// Materials whose `category_id` is NULL are invisible under any category
// filter on /materials (NFM-3917 Tier 1D silent gap). The backend exposes
// the count so the page can render a notice; the count comes from a real
// `COUNT(*)` query and the UI never hardcodes the number.

export interface UncategorizedCountEnvelope {
  readonly count: number;
}

/**
 * Fetch the number of materials with `category_id IS NULL`.
 *
 * Returns 0 on a network error so the UI degrades to "no notice" rather
 * than blocking page render — this is a non-critical disclosure, not a
 * functional dependency of the list view.
 */
export async function getUncategorizedMaterialCount(): Promise<number> {
  try {
    const envelope = await request<ApiResponse<UncategorizedCountEnvelope>>(
      "/api/v1/material-categories/uncategorized-count",
    );
    return envelope.data.count;
  } catch {
    return 0;
  }
}

// ── API functions ─────────────────────────────────────────────────────

/**
 * Fetch paginated properties for a given material.
 *
 * The backend wraps every response in `ApiResponse<T> = { success, data: T,
 * error? }` and the shared `request()` helper does NOT auto-unwrap, so this
 * function destructures `envelope.data` and returns the inner
 * `MaterialPropertyListResponse`. Callers can therefore access
 * `result.data` (the array) and `result.meta.total` directly.
 */
export async function getMaterialProperties(
  materialId: string,
  params: MaterialPropertyListParams = {},
): Promise<MaterialPropertyListResponse> {
  const sp = new URLSearchParams();

  sp.set("page", String(params.page ?? 1));
  sp.set("limit", String(params.limit ?? 50));
  if (params.sort) sp.set("sort", params.sort);
  if (params.order) sp.set("order", params.order);
  if (params.filter) sp.set("filter", params.filter);

  const envelope = await request<ApiResponse<MaterialPropertyListResponse>>(
    `/api/v1/materials/${materialId}/properties?${sp.toString()}`,
  );
  return envelope.data;
}

/**
 * Fetch a material summary by ID.
 *
 * Unwraps the standard `ApiResponse<T>` envelope (see
 * `getMaterialProperties` for the rationale) and returns the inner
 * `MaterialSummary` so callers can read `.name` / `.formula` directly.
 */
export async function getMaterial(
  materialId: string,
): Promise<MaterialSummary> {
  const envelope = await request<ApiResponse<MaterialSummary>>(
    `/api/v1/materials/${materialId}`,
  );
  return envelope.data;
}

// ── Subgraph (NFM-1258) ───────────────────────────────────────────────

/** Raw API node shape returned by the KG graph endpoints. */
export interface KgGraphApiNode {
  readonly id: string;
  readonly label: string;
  readonly type: string;
  readonly properties?: Readonly<Record<string, unknown>>;
}

/** Raw API edge shape returned by the KG graph endpoints. */
export interface KgGraphApiEdge {
  readonly source: string;
  readonly target: string;
  readonly type: string;
}

/** Raw API response from the KG graph endpoints. */
export interface KgGraphApiResponse {
  readonly nodes: ReadonlyArray<KgGraphApiNode>;
  readonly edges: ReadonlyArray<KgGraphApiEdge>;
}

/**
 * Maps an API node-type string (Material / Property / Experiment /
 * Condition / Publication / other) to the simplified public
 * `GraphNodeType` consumed by `GraphCanvas`.
 *
 * Mapping rules (per NFM-1258 spec):
 *   Material            → "material"
 *   Property            → "property"
 *   Experiment / ontology-ish → "entity"
 *   Condition / Publication / Source / other → "default"
 */
export function toGraphNodeType(apiType: string): GraphNodeType {
  const normalized = apiType.toLowerCase();
  if (normalized === "material") return "material";
  if (normalized === "property") return "property";
  if (normalized === "experiment" || normalized === "ontology") return "entity";
  return "default";
}

/**
 * Map a raw KG graph API response to the `GraphData` shape consumed by
 * `GraphCanvas`. Node IDs pass through verbatim (e.g. `material:ZrO2`);
 * edges get a stable `id` synthesized from their source/target.
 */
export function mapSubgraphResponse(
  response: KgGraphApiResponse | { data: KgGraphApiResponse },
): GraphData {
  // The API wraps the payload in a { success, data, error } envelope.
  // request() returns the raw envelope without unwrapping .data,
  // so handle both shapes defensively.
  const payload =
    "data" in response && "nodes" in (response as any).data
      ? (response as any).data
      : response;

  const nodes: GraphNode[] = payload.nodes.map((node: KgGraphApiNode) => ({
    id: node.id,
    label: node.label,
    type: toGraphNodeType(node.type),
  }));

  const edges: GraphEdge[] = payload.edges.map(
    (edge: KgGraphApiEdge, index: number) => ({
      id: `e-${index}-${edge.source}->${edge.target}`,
      source: edge.source,
      target: edge.target,
      type: edge.type,
    }),
  );

  return { nodes, edges };
}

/**
 * Fetch the depth-N KG subgraph rooted at a material node.
 *
 * Maps the API response to `GraphData` for direct consumption by
 * `GraphCanvas`. The backend returns node ids in the form
 * `"material:<id>"`; these pass through verbatim and are stripped only
 * at navigation time.
 *
 * Endpoint contract (NFM-1258.3, NFM-4083):
 *   GET /api/v1/kg/graph/subgraph?nodeId=<id>&depth=<n>
 *
 * NFM-4083 moved this from ``/api/v1/kg/graph`` to ``/kg/graph/subgraph``
 * because the global-pool ``/kg/graph`` endpoint registered first was
 * shadowing it. ``nodeId`` may be either a ``materials.id`` (resolved via
 * the materials→KG bridge on the server) or a ``KGNode`` UUID.
 */
export async function getMaterialSubgraph(
  materialId: string,
  depth = 2,
): Promise<GraphData> {
  const sp = new URLSearchParams();
  sp.set("nodeId", materialId);
  sp.set("depth", String(depth));
  const response = await request<KgGraphApiResponse>(
    `/api/v1/kg/graph/subgraph?${sp.toString()}`,
  );
  return mapSubgraphResponse(response);
}
