import type { MDVerificationJobResponse } from "@/lib/md-verification-api"

/** Sort field for the history list */
export type HistorySortField = "created_at" | "grade"

export type HistorySortOrder = "ascend" | "descend"

export interface HistoryFilters {
  potentialName: string
  status: string | undefined
  dateRange: [string, string] | null
}

export const DEFAULT_FILTERS: HistoryFilters = {
  potentialName: "",
  status: undefined,
  dateRange: null,
}

/** Sort comparator factory for history jobs */
export function compareJobs(
  field: HistorySortField,
  order: HistorySortOrder,
): (
  a: MDVerificationJobResponse,
  b: MDVerificationJobResponse,
) => number {
  const direction = order === "ascend" ? 1 : -1

  return (a, b) => {
    if (field === "created_at") {
      const dateA = a.created_at ? new Date(a.created_at).getTime() : 0
      const dateB = b.created_at ? new Date(b.created_at).getTime() : 0
      return (dateA - dateB) * direction
    }

    return 0
  }
}

/** Diff row for the comparison table */
export interface DiffRow {
  property: string
  valueA: number | string | null
  valueB: number | string | null
  diff: number | null
  diffPercent: number | null
}

/** Compute numeric diff between two values */
export function computeDiff(
  valueA: number | string | null,
  valueB: number | string | null,
): { diff: number | null; diffPercent: number | null } {
  if (
    valueA === null ||
    valueB === null ||
    typeof valueA !== "number" ||
    typeof valueB !== "number"
  ) {
    return { diff: null, diffPercent: null }
  }

  const diff = valueA - valueB
  const diffPercent =
    valueB !== 0 ? (diff / Math.abs(valueB)) * 100 : null

  return { diff, diffPercent }
}
