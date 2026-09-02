"use client"

/**
 * `<DataLossNotice>` — the inline disclosure surface.
 *
 * Spec §2 (props) + §3 (wireframes) + §6 (behavior). The component
 * ONLY renders for `attribution.status === "lost"` rows. The server
 * has already filtered to the 4-canonical cohort (§4.2); this
 * component never reaches for a `data_sources` join.
 *
 * Layout (per §2):
 *   [icon] [headline · date]  →  popover on focus/click/dismiss
 *
 * Visual tokens (per §1):
 *   • background  var(--color-surface-elevated)
 *   • border-left  4px var(--warning-border)
 *   • text         var(--color-text)
 *
 * Sub-animation respects `prefers-reduced-motion`.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import type { JSX } from "react"

import "./data-loss-notice.css"

import { DataLossNoticeIcon } from "./DataLossNoticeIcon"
import { emitDataLossEvent } from "./analytics"
import { formatCreatedAt, getMessages } from "./messages"
import { resolveFeatureFlag } from "./feature-flag"
import type {
  DataLossLocale,
  DataLossNoticeProps,
} from "./types"
import { useDataLossNotice } from "./useDataLossNotice"

const DEFAULT_LOST_AT = "2026-09-02"
const DEFAULT_LOCALE: DataLossLocale = "en"

export function DataLossNotice(props: DataLossNoticeProps): JSX.Element | null {
  const {
    variant,
    measurementId,
    attribution,
    popoverPlacement = "top",
    language,
    onLearnMoreHref,
    onLearnMoreLabel,
    surface,
    datasetId,
  } = props

  const { isDismissed, dismiss } = useDataLossNotice(measurementId)
  const locale: DataLossLocale = language ?? DEFAULT_LOCALE
  const messages = getMessages(locale)

  const [popoverOpen, setPopoverOpen] = useState(false)
  const openedAtRef = useRef<number | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  // Cohort + copy inputs are computed BEFORE the render guards below:
  // every hook must run unconditionally (rules-of-hooks). The provider
  // can flip the feature flag at runtime and an attribution refetch can
  // flip a row intact → lost, so a mounted component's guard outcome
  // can change between renders — early-returning above the hooks would
  // crash React with "Rendered fewer hooks than expected".
  const isLost = attribution?.status === "lost"
  const siblingPlaceholderCount = isLost
    ? attribution.siblingPlaceholderCount ?? 0
    : 0

  // Fire `shown` analytics once when popover first opens.
  useEffect((): (() => void) | void => {
    if (!popoverOpen) return undefined
    if (openedAtRef.current === null) {
      openedAtRef.current = Date.now()
      emitDataLossEvent("dataloss_notice_shown", {
        measurementId,
        datasetId,
        siblingPlaceholderCount,
        surface,
        locale,
      })
      emitDataLossEvent("data_loss_notice.viewed", {
        measurementId,
        datasetId,
        siblingPlaceholderCount,
        surface,
        locale,
      })
    }
    return (): void => {
      // Close-time dwell metric. Captured only when popover actually
      // closes (unmount), not on every re-render.
      const opened = openedAtRef.current
      openedAtRef.current = null
      if (opened) {
        const dwell = Date.now() - opened
        if (dwell > 0) {
          // No additional analytics event — the spec AC only requires
          // `_shown` + `_dismissed` + `_learn_more_clicked`. The dwell
          // here is left as an internal metric; left commented so future
          // events can be added without re-reading the spec.
          void dwell
        }
      }
    }
  }, [
    popoverOpen,
    measurementId,
    datasetId,
    siblingPlaceholderCount,
    surface,
    locale,
  ])

  // Click-away / escape to close.
  useEffect((): (() => void) | void => {
    if (!popoverOpen) return undefined
    const onClickAway = (e: MouseEvent): void => {
      const target = e.target as Node | null
      if (!target) return
      if (popoverRef.current?.contains(target)) return
      if (triggerRef.current?.contains(target)) return
      setPopoverOpen(false)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setPopoverOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener("mousedown", onClickAway)
    document.addEventListener("keydown", onKey)
    return (): void => {
      document.removeEventListener("mousedown", onClickAway)
      document.removeEventListener("keydown", onKey)
    }
  }, [popoverOpen])

  const onTriggerClick = useCallback((): void => {
    setPopoverOpen((v) => !v)
  }, [])

  const onDismiss = useCallback((): void => {
    dismiss()
    setPopoverOpen(false)
    emitDataLossEvent("dataloss_notice_dismissed", {
      measurementId,
      datasetId,
      siblingPlaceholderCount,
      surface,
      locale,
    })
    emitDataLossEvent("data_loss_notice.dismissed", {
      measurementId,
      datasetId,
      siblingPlaceholderCount,
      surface,
      locale,
    })
  }, [
    dismiss,
    measurementId,
    datasetId,
    siblingPlaceholderCount,
    surface,
    locale,
  ])

  const onLearnMore = useCallback((): void => {
    emitDataLossEvent("dataloss_notice_learn_more_clicked", {
      measurementId,
      datasetId,
      siblingPlaceholderCount,
      surface,
      locale,
    })
    emitDataLossEvent("data_loss_notice.learn_more_clicked", {
      measurementId,
      datasetId,
      siblingPlaceholderCount,
      surface,
      locale,
    })
  }, [
    measurementId,
    datasetId,
    siblingPlaceholderCount,
    surface,
    locale,
  ])

  // Render guards — intentionally below every hook (see the comment
  // above). Filter to the lost cohort only: the server contract (§5.2)
  // may also return `intact` rows, in which case the component MUST
  // not render.
  if (variant !== "inline") return null
  if (!attribution || attribution.status !== "lost") return null

  // Spec §6.1: when flag is OFF (or no provider), render nothing.
  const flag = resolveFeatureFlag()
  if (!flag.enabled) return null

  const createdAt = formatCreatedAt(attribution.lostAt, DEFAULT_LOST_AT)

  const interpolated = {
    headline: messages.headline,
    body: messages.body.replace(
      "{siblingPlaceholderCount}",
      String(siblingPlaceholderCount),
    ),
    forwardLook: messages.forwardLook,
    inlineLabel: messages.inlineLabel(createdAt),
  }

  return (
    <span
      className="data-loss-notice"
      data-variant="inline"
      data-surface={surface ?? null}
      data-dismissed={isDismissed ? "true" : "false"}
      data-testid="data-loss-notice"
    >
      <button
        ref={triggerRef}
        type="button"
        className="data-loss-notice__trigger"
        aria-expanded={popoverOpen}
        aria-haspopup="dialog"
        aria-label={interpolated.inlineLabel}
        data-testid="data-loss-notice-trigger"
        onClick={onTriggerClick}
      >
        <DataLossNoticeIcon size={14} />
        <span className="data-loss-notice__label">
          {interpolated.inlineLabel}
        </span>
      </button>

      {popoverOpen && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label={interpolated.headline}
          aria-describedby={`dataloss-body-${measurementId}`}
          className={`data-loss-notice__popover data-loss-notice__popover--${popoverPlacement}`}
          data-testid="data-loss-notice-popover"
        >
          <div className="data-loss-notice__headline">
            <DataLossNoticeIcon size={18} />
            <span>{interpolated.headline}</span>
          </div>
          {isDismissed ? (
            <p className="data-loss-notice__previously">
              {messages.previouslyDismissed}
            </p>
          ) : (
            <>
              <p
                id={`dataloss-body-${measurementId}`}
                className="data-loss-notice__body"
              >
                {interpolated.body}
              </p>
              <p className="data-loss-notice__forward-look">
                {interpolated.forwardLook}
              </p>
            </>
          )}
          <div className="data-loss-notice__actions">
            {onLearnMoreHref ? (
              <a
                href={onLearnMoreHref}
                className="data-loss-notice__learn-more"
                data-testid="data-loss-notice-learn-more"
                onClick={onLearnMore}
              >
                {onLearnMoreLabel ?? messages.learnMoreLabel}
              </a>
            ) : null}
            <button
              type="button"
              className="data-loss-notice__dismiss"
              data-testid="data-loss-notice-dismiss"
              onClick={onDismiss}
            >
              {messages.dismissLabel}
            </button>
          </div>
        </div>
      )}
    </span>
  )
}