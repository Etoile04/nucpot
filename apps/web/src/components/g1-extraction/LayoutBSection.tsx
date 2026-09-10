/**
 * LayoutBSection — wrapper that mounts LayoutBPanel inside the legacy
 * LiteratureDetailView, adapting the v3/v4 `extraction_results` array
 * to the G1 row contract.
 *
 * NFM-4553 G1-E — wire Layout B (default literature detail view per
 * docs/specs/G1-extraction-value-presentation.md §4.1 + AC-3) into
 * the existing detail page WITHOUT removing the legacy extraction
 * results table below it. The legacy surface stays reachable so
 * domain experts can cross-reference during the transition.
 */

import { useCallback } from "react"
import { LayoutBPanel } from "@/components/g1-extraction/LayoutBPanel"
import type { G1PropertyMeasurement, ReviewAction } from "@/lib/g1-extraction/types"
import { REVIEW_ACTION_TO_STATUS } from "@/lib/g1-extraction/types"
import { message } from "antd"

interface LayoutBSectionProps {
  readonly materialLabel: string
  readonly measurements: readonly G1PropertyMeasurement[]
  /** Optional: PATCH hook — falls back to a no-op + toast. */
  readonly onReviewAction?: (
    measurement: G1PropertyMeasurement,
    action: ReviewAction,
  ) => Promise<void>
}

export function LayoutBSection({
  materialLabel,
  measurements,
  onReviewAction,
}: LayoutBSectionProps) {
  const handleAction = useCallback(
    async (m: G1PropertyMeasurement, action: ReviewAction) => {
      if (onReviewAction) {
        await onReviewAction(m, action)
        return
      }
      // Default fallback: surface the action via toast so the UI
      // is observably responsive during demo / pre-backend-integration.
      // The Integration task (NFM-4556) wires the real PATCH.
      const status = REVIEW_ACTION_TO_STATUS[action]
      void message.info(
        `[Layout B / 校对预览] ${m.property_name} → ${status}`,
        3,
      )
    },
    [onReviewAction],
  )

  return (
    <section
      data-testid="layout-b-section"
      aria-label="布局 B / Layout B"
      className="rounded border border-gray-700 bg-gray-900/30 p-3"
    >
      <LayoutBPanel
        materialLabel={materialLabel}
        measurements={measurements}
        onReviewAction={handleAction}
      />
    </section>
  )
}

export default LayoutBSection
