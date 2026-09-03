"use client"

/**
 * Layout-level reactive gate for the DATA_LOSS_NOTICE flag (NFM-4180).
 *
 * NFM-4146 resolved the flag once at layout render (build-time env
 * var), so killing or widening the rollout required a redeploy. This
 * gate is the NFM-4180 answer: it owns a `flagEnabled` state that
 * starts `false` (fail closed), fetches the flag-service evaluation on
 * mount, and re-checks every 60s — so an operator toggle
 * (`PUT /api/v1/feature-flags/DATA_LOSS_NOTICE`) reaches clients within
 * a minute with no redeploy.
 *
 * All effects are client-side; localStorage (flag subject id) is only
 * touched inside the effect, so SSR/hydration never sees a stale value.
 */

import * as React from "react"

import { DataLossNoticeProvider } from "@/components/data-loss-notice/DataLossNoticeProvider"
import { refreshFeatureFlag } from "@/components/data-loss-notice/feature-flag"

/** Re-check interval for the flag service. Kill-switch latency bound. */
const FLAG_REFRESH_INTERVAL_MS = 60_000

export interface DataLossNoticeGateProps {
  readonly children: React.ReactNode
}

export function DataLossNoticeGate({ children }: DataLossNoticeGateProps) {
  const [flagEnabled, setFlagEnabled] = React.useState(false)

  React.useEffect(() => {
    let alive = true

    const check = async () => {
      const value = await refreshFeatureFlag()
      if (alive) setFlagEnabled(value)
    }

    void check()
    const intervalId = window.setInterval(() => void check(), FLAG_REFRESH_INTERVAL_MS)

    return () => {
      alive = false
      window.clearInterval(intervalId)
    }
  }, [])

  return (
    <DataLossNoticeProvider forceEnabled={flagEnabled}>
      {children}
    </DataLossNoticeProvider>
  )
}
