import { useEffect, useRef, useCallback, useState } from "react"
import {
  listMDVerificationJobs,
  type MDVerificationJobResponse,
  type MDVerificationJobListResponse,
  type JobStatus,
} from "@/lib/md-verification-api"

interface UseTaskPollingOptions {
  /** Polling interval in milliseconds. Default: 30_000 (30s) */
  interval?: number
  /** Page size for listing. Default: 100 */
  pageSize?: number
  /** Additional status filters to poll. Default: [pending, submitted, running] */
  activeStatuses?: JobStatus[]
  /** Element system filter */
  elementSystem?: string
  /** Whether polling is enabled. Default: true */
  enabled?: boolean
}

interface UseTaskPollingReturn {
  /** Current list of active jobs */
  jobs: MDVerificationJobResponse[]
  /** Whether a poll is in progress */
  isPolling: boolean
  /** Number of polls completed */
  pollCount: number
  /** Trigger an immediate manual refresh */
  refresh: () => Promise<void>
}

const DEFAULT_INTERVAL = 30_000
const DEFAULT_ACTIVE_STATUSES: JobStatus[] = [
  "pending" as JobStatus,
  "submitted" as JobStatus,
  "running" as JobStatus,
]

function fetchActiveJobs(
  activeStatuses: readonly JobStatus[],
  elementSystem?: string,
): Promise<MDVerificationJobListResponse> {
  return listMDVerificationJobs({
    status: undefined,
    element_system: elementSystem,
    limit: 100,
    offset: 0,
  }).then((result) => ({
    ...result,
    jobs: result.jobs.filter((job) =>
      activeStatuses.includes(job.status as JobStatus),
    ),
  }))
}

export function useTaskPolling(
  options: UseTaskPollingOptions = {},
): UseTaskPollingReturn {
  const {
    interval = DEFAULT_INTERVAL,
    activeStatuses = DEFAULT_ACTIVE_STATUSES,
    elementSystem,
    enabled = true,
  } = options

  const [jobs, setJobs] = useState<MDVerificationJobResponse[]>([])
  const [isPolling, setIsPolling] = useState(false)
  const [pollCount, setPollCount] = useState(0)

  const enabledRef = useRef(enabled)
  const activeStatusesRef = useRef(activeStatuses)
  const elementSystemRef = useRef(elementSystem)
  const pollCountRef = useRef(0)

  // Keep refs in sync with props
  enabledRef.current = enabled
  activeStatusesRef.current = activeStatuses
  elementSystemRef.current = elementSystem

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return

    setIsPolling(true)
    try {
      const result = await fetchActiveJobs(
        activeStatusesRef.current,
        elementSystemRef.current,
      )
      setJobs(result.jobs)
      pollCountRef.current += 1
      setPollCount(pollCountRef.current)
    } catch {
      // Silently retry on next interval — don't disrupt UI
    } finally {
      setIsPolling(false)
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    refresh()
  }, [refresh])

  // Polling interval
  useEffect(() => {
    if (!enabled) return

    const timer = setInterval(() => {
      refresh()
    }, interval)

    return () => clearInterval(timer)
  }, [enabled, interval, refresh])

  return { jobs, isPolling, pollCount, refresh }
}
