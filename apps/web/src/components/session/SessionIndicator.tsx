'use client'

import { Tag } from 'antd'
import {
  ClockCircleOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useSessionRemaining } from './useSessionRemaining'

/**
 * Session-countdown UI surface (NFM-2253, B side of NFM-2236).
 *
 * Spec: [NFM-2251 design-spec](/NFM/issues/NFM-2251) §2 and §5.1.
 *
 * A pure presentation component. Reads `state` and `remainingSeconds`
 * from `useSessionRemaining()` and renders an AntD `Tag` inside the
 * existing `Nav` row. Hidden (returns null, not just visibility:hidden)
 * when there is no session or when the session has expired — the
 * re-auth modal takes over in the expired case.
 *
 * Lifecycle boundary: the tab-visibility listener lives in
 * `SessionProvider` (NFM-2252) per NFM-2251 §2.5. By the time `remaining`
 * reaches this component, it has already been reconciled to the true
 * value; the indicator just renders it.
 */

const WARNING_THRESHOLD_SECONDS = 120
const ERROR_THRESHOLD_SECONDS = 30
const HOUR_IN_SECONDS = 3600

type IndicatorBand = 'ok' | 'warning' | 'error' | 'refreshing'

function computeBand(remainingSeconds: number): Exclude<IndicatorBand, 'refreshing'> {
  if (remainingSeconds < ERROR_THRESHOLD_SECONDS) return 'error'
  if (remainingSeconds < WARNING_THRESHOLD_SECONDS) return 'warning'
  return 'ok'
}

function pad2(n: number): string {
  return n.toString().padStart(2, '0')
}

/**
 * Pure time-format helpers. Centralised in this component so the spec
 * wording is the only source of truth and so NFM-2254 (ReAuthPrompt)
 * can reuse the same formatter. Exported for unit tests.
 */

export function formatRemainingMain(remainingSeconds: number): string {
  const safe = Math.max(0, Math.floor(remainingSeconds))
  if (safe >= HOUR_IN_SECONDS) {
    const h = Math.floor(safe / HOUR_IN_SECONDS)
    const mm = Math.floor((safe % HOUR_IN_SECONDS) / 60)
    const ss = safe % 60
    return `${h}:${pad2(mm)}:${pad2(ss)}`
  }
  const mm = Math.floor(safe / 60)
  const ss = safe % 60
  return `${pad2(mm)}:${pad2(ss)}`
}

export function buildIndicatorCopy(
  remainingSeconds: number,
  band: Exclude<IndicatorBand, 'refreshing'>,
): string {
  if (band === 'error') {
    return `会话即将过期 ${Math.max(0, Math.floor(remainingSeconds))} 秒`
  }
  if (band === 'warning') {
    return `会话即将到期 ${formatRemainingMain(remainingSeconds)}`
  }
  return `会话剩余 ${formatRemainingMain(remainingSeconds)}`
}

export function buildIndicatorAria(remainingSeconds: number, band: IndicatorBand): string {
  if (band === 'refreshing') return '正在刷新会话'
  if (band === 'error') {
    return `会话即将过期，剩余 ${Math.max(0, Math.floor(remainingSeconds))} 秒，请保存工作`
  }
  if (band === 'warning') {
    return `会话即将到期，剩余 ${formatRemainingMain(remainingSeconds)}`
  }
  const total = Math.max(0, Math.floor(remainingSeconds))
  const mm = Math.floor(total / 60)
  const ss = total % 60
  return `会话剩余 ${mm} 分 ${ss} 秒`
}

export default function SessionIndicator(): ReactElement | null {
  const { state, remainingSeconds } = useSessionRemaining()

  // Spec §2.2 — hidden (not visible:none) when there's no session.
  // The re-auth modal owns the user's attention once the session is
  // expired; the indicator would compete with it.
  if (state === 'unauthenticated' || state === 'expired') {
    return null
  }

  const band: IndicatorBand =
    state === 'refreshing' ? 'refreshing' : computeBand(remainingSeconds)

  if (band === 'refreshing') {
    return (
      <Tag
        color="processing"
        bordered={false}
        icon={<SyncOutlined spin aria-hidden="true" />}
        aria-live="polite"
        aria-atomic="true"
        aria-label="正在刷新会话"
        data-state="refreshing"
        data-remaining-seconds={remainingSeconds}
        tabIndex={-1}
      >
        刷新中…
      </Tag>
    )
  }

  const color = band === 'error' ? 'error' : band === 'warning' ? 'warning' : 'default'
  const icon =
    band === 'error' ? (
      <WarningOutlined aria-hidden="true" />
    ) : (
      <ClockCircleOutlined aria-hidden="true" />
    )
  const isAssertive = band === 'error'

  return (
    <Tag
      color={color}
      bordered={false}
      icon={icon}
      aria-live={isAssertive ? 'assertive' : 'polite'}
      aria-atomic="true"
      aria-label={buildIndicatorAria(remainingSeconds, band)}
      data-state={band}
      data-remaining-seconds={remainingSeconds}
      tabIndex={-1}
    >
      {buildIndicatorCopy(remainingSeconds, band)}
    </Tag>
  )
}
