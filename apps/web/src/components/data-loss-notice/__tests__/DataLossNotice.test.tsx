/**
 * Vitest coverage for DataLossNotice (NFM-4146, spec §7 backstop).
 *
 * Test surface mirrors the §7 anti-regression checklist:
 *   1. Flag OFF → component renders null.
 *   2. Status: "intact" → component renders null.
 *   3. Status: "lost" + flag ON → renders the trigger + headline copy.
 *   4. Popover opens on click; dismiss fires the analytics event.
 *   5. Learn-more fires the analytics event.
 *   6. ZH-CN locale renders the localized headline.
 *   7. Dismissed state shows the "previously dismissed" affordance.
 */

import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { useState } from "react"
import type { JSX } from "react"

import {
  DataLossNotice,
  DataLossNoticeProvider,
} from "../index"
import { setRuntimeOverride } from "../feature-flag"
import {
  subscribeDataLossEvents,
  type DataLossEventName,
  type DataLossEventProps,
} from "../analytics"

interface EventCapture {
  name: DataLossEventName
  props: DataLossEventProps
}

const LOST_ATTR = {
  status: "lost" as const,
  lostAt: "2026-09-02",
  siblingPlaceholderCount: 4,
}

const INTACT_ATTR = { status: "intact" as const }

function ProviderWrapper({
  forceEnabled,
  children,
}: {
  forceEnabled?: boolean | null
  children: React.ReactNode
}): JSX.Element {
  return (
    <DataLossNoticeProvider forceEnabled={forceEnabled ?? null}>
      {children}
    </DataLossNoticeProvider>
  )
}

describe("DataLossNotice", (): void => {
  beforeEach((): void => {
    window.localStorage.clear()
    setRuntimeOverride(null)
  })

  afterEach((): void => {
    setRuntimeOverride(null)
  })

  it("renders null when the feature flag is OFF", (): void => {
    setRuntimeOverride(false)
    const { container } = render(
      <ProviderWrapper forceEnabled={false}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
        />
      </ProviderWrapper>,
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders null when attribution.status is intact", (): void => {
    setRuntimeOverride(true)
    const { container } = render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={INTACT_ATTR}
        />
      </ProviderWrapper>,
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders the trigger + inline label when status is lost and flag is ON", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )
    const trigger = screen.getByTestId("data-loss-notice-trigger")
    expect(trigger).toBeInTheDocument()
    expect(trigger.textContent).toContain("2026-09-02")
  })

  it("opens the popover on click and dismisses on button click", (): void => {
    setRuntimeOverride(true)
    const captured: EventCapture[] = []
    const unsubscribe = subscribeDataLossEvents((name, props): void => {
      captured.push({ name, props })
    })

    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(screen.getByTestId("data-loss-notice-popover")).toBeInTheDocument()
    expect(
      captured.some((e): boolean => e.name === "dataloss_notice_shown"),
    ).toBe(true)

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-dismiss"))
    })

    expect(
      captured.some(
        (e): boolean => e.name === "dataloss_notice_dismissed",
      ),
    ).toBe(true)

    unsubscribe()
  })

  it("fires the learn-more analytics event when the link is clicked", (): void => {
    setRuntimeOverride(true)
    const captured: EventCapture[] = []
    const unsubscribe = subscribeDataLossEvents((name, props): void => {
      captured.push({ name, props })
    })

    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          onLearnMoreHref="/blog/2026-09-02-attribution-cleanup"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-learn-more"))
    })

    expect(
      captured.some(
        (e): boolean =>
          e.name === "dataloss_notice_learn_more_clicked",
      ),
    ).toBe(true)

    unsubscribe()
  })

  it("renders the zh-CN localized copy", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          language="zh-CN"
        />
      </ProviderWrapper>,
    )
    expect(
      screen.getByTestId("data-loss-notice-trigger").textContent,
    ).toContain("来源信息缺失")
  })

  it("substitutes {siblingPlaceholderCount} from the attribution payload", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={{
            status: "lost",
            lostAt: "2026-09-02",
            siblingPlaceholderCount: 7,
          }}
        />
      </ProviderWrapper>,
    )
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(
      screen.getByTestId("data-loss-notice-popover").textContent,
    ).toContain("7")
  })

  it("shows the previously-dismissed affordance after dismissal", (): void => {
    setRuntimeOverride(true)
    function Harness(): JSX.Element {
      const [version, setVersion] = useState(0)
      return (
        <ProviderWrapper forceEnabled={true}>
          <DataLossNotice
            variant="inline"
            measurementId="m-1"
            attribution={LOST_ATTR}
            key={String(version)}
          />
          <button
            data-testid="rerender"
            onClick={(): void => setVersion((v) => v + 1)}
          >
            rerender
          </button>
        </ProviderWrapper>
      )
    }
    render(<Harness />)
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-dismiss"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("rerender"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(
      screen.getByTestId("data-loss-notice-popover").textContent,
    ).toContain("previously dismissed")
  })
})