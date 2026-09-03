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
 *
 * NFM-4238 — the popover is portaled out of the trigger's `<span>`
 * via `createPortal(..., document.body)` and positioned with
 * `position: fixed` from `triggerRef.getBoundingClientRect()`. On
 * property-detail the notice mounts inside `td.ant-table-cell-ellipsis`
 * (`overflow: hidden`) within antd Table's `scroll.x` scroll container
 * (`.ant-table-content`, `overflow: auto`), so the old in-cell popover
 * was clipped by BOTH ancestors at 1440/768/375 — disclosure text and
 * dismiss control were invisible to mouse users. Portaling moves the
 * dialog node out of every ancestor clip while preserving the §5.2
 * field contract, testids, copy, and analytics unchanged (AC-4 CPO
 * ruling — CONTRACT RESTORATION, not redesign). The popover clamps to
 * stay inside the viewport and follows the trigger on scroll/resize
 * via viewport-relative coords.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import type { CSSProperties, JSX } from "react"
import { createPortal } from "react-dom"

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

// `useLayoutEffect` fires the SSR warning when the component renders on
// the server ("use client" components are still SSR'd by Next), even
// though the effect body only matters after a client-side click. Swap
// in `useEffect` on the server to keep the console clean.
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect

// Viewport margin kept clear around the fixed popover (matches the 8px
// offset the old absolute CSS used between trigger and popover).
const POPOVER_VIEWPORT_MARGIN = 8

// Popover box dimensions — read from CSS (max-width: 360px) so the JS
// clamp knows the actual rendered size without measuring on every
// frame. If the CSS changes this width, update here too.
const POPOVER_MAX_WIDTH = 360

interface PopoverPlacement {
  readonly top: number
  readonly left: number
}

/**
 * Compute the popover's top-left (viewport coords) given the trigger's
 * bounding rect, the requested placement, and the popover's intrinsic
 * size. Clamps to the viewport with `POPOVER_VIEWPORT_MARGIN` clear
 * space so the popover never appears flush against (or beyond) the
 * edge.
 *
 * The placement modifiers in `data-loss-notice.css` (`--top`, `--right`,
 * `--bottom`) became inert when the popover moved to a portal — this
 * function replaces them.
 */
function computePopoverPlacement(
  trigger: DOMRect,
  placement: "top" | "right" | "bottom",
  popoverWidth: number,
  popoverHeight: number,
): PopoverPlacement {
  const margin = POPOVER_VIEWPORT_MARGIN
  const viewportWidth =
    typeof window === "undefined" ? 0 : window.innerWidth
  const viewportHeight =
    typeof window === "undefined" ? 0 : window.innerHeight

  // Default X = align with the trigger's left edge (matches the old
  // absolute CSS for `--top` / `--bottom`).
  let left = trigger.left
  // Default Y varies by placement — see each branch.
  let top = 0

  if (placement === "top") {
    top = trigger.top - popoverHeight - margin
  } else if (placement === "bottom") {
    top = trigger.bottom + margin
  } else {
    // "right": vertical-centered against the trigger's midline, offset
    // to the trigger's right edge with the standard margin gap. This
    // mirrors the old `--right` modifier (`left: calc(100% + 8px);
    // top: 50%; transform: translateY(-50%)`).
    left = trigger.right + margin
    top = trigger.top + trigger.height / 2 - popoverHeight / 2
  }

  // Clamp horizontally so the popover stays inside the viewport.
  const maxLeft = Math.max(
    margin,
    viewportWidth - popoverWidth - margin,
  )
  if (left < margin) left = margin
  if (left > maxLeft) left = maxLeft

  // Clamp vertically — the "top" placement can otherwise overflow when
  // the trigger sits near the top of the viewport; the "right"
  // placement can overflow on short viewports.
  const maxTop = Math.max(
    margin,
    viewportHeight - popoverHeight - margin,
  )
  if (top < margin) top = margin
  if (top > maxTop) top = maxTop

  return { top, left }
}

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
  const [popoverCoords, setPopoverCoords] =
    useState<PopoverPlacement | null>(null)
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

  // Click-away / escape to close. The click-away handler is ref-based
  // (it tests `popoverRef.current?.contains(target)` and
  // `triggerRef.current?.contains(target)`), so it keeps working when
  // the popover node is portaled to `document.body` — `contains()`
  // works against any DOM node regardless of where it mounts in the
  // tree. Don't rewrite this handler when portaling: the classic
  // portal regression is rewriting it to close on the popover's own
  // click.
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

  // Position the portaled popover from the trigger's viewport rect.
  // Runs in a layout effect so the first paint already has the right
  // coordinates — no flash-of-unpositioned-popover. Recomputes on
  // scroll and resize so the popover cannot visually detach from its
  // row when the table scrolls (`.ant-table-content`) or the window
  // resizes.
  useIsomorphicLayoutEffect((): (() => void) | void => {
    if (!popoverOpen) return undefined
    const trigger = triggerRef.current
    if (!trigger) return undefined

    const recompute = (): void => {
      // Read the popover's actual rendered size after the layout has
      // settled so the clamp uses real dimensions, not the CSS min/max.
      const popoverEl = popoverRef.current
      const popoverWidth = popoverEl?.offsetWidth ?? POPOVER_MAX_WIDTH
      const popoverHeight = popoverEl?.offsetHeight ?? 0
      const rect = trigger.getBoundingClientRect()
      setPopoverCoords(
        computePopoverPlacement(
          rect,
          popoverPlacement,
          popoverWidth,
          popoverHeight,
        ),
      )
    }

    recompute()
    window.addEventListener("scroll", recompute, true)
    window.addEventListener("resize", recompute)
    return (): void => {
      window.removeEventListener("scroll", recompute, true)
      window.removeEventListener("resize", recompute)
    }
  }, [popoverOpen, popoverPlacement])

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

  // The popover is portaled to `document.body` so it escapes every
  // ancestor `overflow: hidden` (notably `td.ant-table-cell-ellipsis`
  // and `.ant-table-content`). Coords are viewport-relative, so the CSS
  // uses `position: fixed`. The dialog node's testids, ARIA, and
  // content are byte-identical to the pre-portal version — this is
  // contract restoration, not redesign.
  const popoverStyle: CSSProperties | undefined = popoverCoords
    ? {
        position: "fixed",
        top: popoverCoords.top,
        left: popoverCoords.left,
        // Width is bound by the CSS `min-width`/`max-width` — the JS
        // clamp assumes those bounds. Setting an explicit `width` here
        // would force a fixed size; leaving it unset preserves the
        // CSS-driven responsive sizing.
      }
    : undefined

  const popover =
    popoverOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popoverRef}
            role="dialog"
            aria-label={interpolated.headline}
            aria-describedby={`dataloss-body-${measurementId}`}
            className="data-loss-notice__popover"
            style={popoverStyle}
            data-testid="data-loss-notice-popover"
            data-placement={popoverPlacement}
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
          </div>,
          document.body,
        )
      : null

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

      {popover}
    </span>
  )
}