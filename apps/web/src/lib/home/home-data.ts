/**
 * Homepage aggregate data (NFM-4990 IA-REFACTOR P1 — 首页五要素).
 *
 * Server-side fetchers for the five-element homepage:
 *   1. Hero            — static copy + HeroSearch client component
 *   2. 统计数据区       — real totals from public v1 endpoints
 *   3. 热门势函数       — /api/v1/potentials?sort=downloads (NFM-4309 counter)
 *   4. 最新更新区       — /api/v1/stats recent_potentials
 *   5. 体系导航区       — /api/v1/material-categories slugs → /materials links
 *
 * Every fetch is fail-soft: a failed source yields `null` and the section
 * renders its honest empty state — the homepage must never fabricate
 * numbers (acceptance: 统计数与 API 一致) nor 5xx because one endpoint
 * is down.
 *
 * The base URL resolution mirrors lib/blog/public-posts.ts (NFM-4940):
 * API_SERVER_URL first, then the Docker-internal service DNS so SSR
 * resolves inside any container.
 */

const FETCH_TIMEOUT_MS = 8_000

function apiBaseUrl(): string {
  return process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"
}

function fetchOptions(): RequestInit {
  return {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  } as RequestInit
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${apiBaseUrl()}${path}`, fetchOptions())
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

/** 2. 统计数据区 — one card per real, public count. */
export interface HomeStats {
  readonly potentials: number | null
  readonly materials: number | null
  readonly literature: number | null
  readonly measurements: number | null
}

/** 3. 热门势函数 — ranked by the NFM-4309 download counter. */
export interface HomePopularPotential {
  readonly id: string
  readonly name: string
  readonly displayName: string | null
  readonly type: string
  readonly elements: readonly string[]
  readonly description: string | null
  readonly downloadCount: number
}

/** 4. 最新更新区 — most recently ingested potentials. */
export interface HomeRecentPotential {
  readonly id: string
  readonly name: string
  readonly displayName: string | null
  readonly type: string
  readonly elements: readonly string[]
  readonly createdAt: string | null
}

/** 5. 体系导航区 — the four ruling-mandated material groups. */
export interface HomeSystemGroup {
  readonly key: string
  readonly title: string
  readonly description: string
  readonly href: string
}

export interface HomeData {
  readonly stats: HomeStats
  readonly popular: readonly HomePopularPotential[] | null
  readonly recent: readonly HomeRecentPotential[] | null
  readonly systems: readonly HomeSystemGroup[] | null
}

/** The four groups from the NFM-4984 ruling (金属燃料·氧化物燃料·包壳·裂变气体). */
const SYSTEM_GROUPS: readonly {
  key: string
  title: string
  description: string
  slug?: string
  fallbackHref: string
}[] = [
  {
    key: "metallic_fuel",
    title: "金属燃料",
    description: "U-Zr、U-Mo 等金属与合金燃料体系",
    slug: "metallic_fuel",
    fallbackHref: "/materials",
  },
  {
    key: "oxide_fuel",
    title: "氧化物燃料",
    description: "UO₂、MOX 等陶瓷氧化物燃料体系",
    slug: "oxide_fuel",
    fallbackHref: "/materials",
  },
  {
    key: "cladding_alloy",
    title: "包壳",
    description: "锆合金等包壳与结构材料体系",
    slug: "cladding_alloy",
    fallbackHref: "/materials",
  },
  {
    // No material category exists for fission gases yet — link to the
    // potentials library pre-filtered to He (Xe/Kr have no potentials
    // today), the only live fission-gas view the data supports in P1.
    key: "fission_gas",
    title: "裂变气体",
    description: "Xe / Kr / He 裂变气体相关势函数",
    fallbackHref: "/potentials?elements=He",
  },
]

interface CategoryItem {
  readonly id: string
  readonly slug: string
}

interface StatsEnvelope {
  readonly data?: {
    readonly total_potentials?: number
    readonly recent_potentials?: readonly {
      id: string
      name: string
      display_name: string | null
      type: string
      elements: readonly string[]
      created_at: string | null
    }[]
  }
}

interface MaterialsEnvelope {
  readonly data?: { readonly total?: number }
}

interface LiteratureEnvelope {
  readonly data?: { readonly total?: number }
}

interface PropertiesStatsEnvelope {
  readonly data?: { readonly total_measurements?: number }
}

interface PopularEnvelope {
  readonly data?: {
    readonly potentials?: readonly {
      id: string
      name: string
      display_name: string | null
      type: string
      elements: readonly string[]
      description: string | null
      download_count?: number
    }[]
  }
}

interface CategoriesEnvelope {
  readonly data?: { readonly items?: readonly CategoryItem[] }
}

export async function getHomeData(): Promise<HomeData> {
  const [statsRes, materialsRes, literatureRes, propertiesRes, popularRes, categoriesRes] =
    await Promise.all([
      getJson<StatsEnvelope>("/api/v1/stats"),
      getJson<MaterialsEnvelope>("/api/v1/materials?limit=1"),
      getJson<LiteratureEnvelope>("/api/v1/literature?limit=1"),
      getJson<PropertiesStatsEnvelope>("/api/v1/properties/stats"),
      getJson<PopularEnvelope>("/api/v1/potentials?sort=downloads&limit=8"),
      getJson<CategoriesEnvelope>("/api/v1/material-categories"),
    ])

  const categories = categoriesRes?.data?.items ?? null
  const systems: readonly HomeSystemGroup[] | null =
    categories === null
      ? null
      : SYSTEM_GROUPS.map((group) => {
          const match = categories.find((c) => c.slug === group.slug)
          return {
            key: group.key,
            title: group.title,
            description: group.description,
            href: match ? `/materials?category_id=${match.id}` : group.fallbackHref,
          }
        })

  const popularItems = popularRes?.data?.potentials ?? null

  return {
    stats: {
      potentials: statsRes?.data?.total_potentials ?? null,
      materials: materialsRes?.data?.total ?? null,
      literature: literatureRes?.data?.total ?? null,
      measurements: propertiesRes?.data?.total_measurements ?? null,
    },
    popular:
      popularItems === null
        ? null
        : popularItems.map((p) => ({
            id: p.id,
            name: p.name,
            displayName: p.display_name ?? null,
            type: p.type,
            elements: p.elements ?? [],
            description: p.description ?? null,
            downloadCount: typeof p.download_count === "number" ? p.download_count : 0,
          })),
    recent:
      statsRes?.data?.recent_potentials === undefined
        ? null
        : (statsRes.data.recent_potentials ?? []).map((p) => ({
            id: p.id,
            name: p.name,
            displayName: p.display_name ?? null,
            type: p.type,
            elements: p.elements ?? [],
            createdAt: p.created_at ?? null,
          })),
    systems,
  }
}
