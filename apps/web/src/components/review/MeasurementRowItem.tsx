"use client"

/**
 * MeasurementRowItem — single sidebar row in Layout B (NFM-4553).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1 + §4.3
 *       docs/specs/G1-extraction-value-presentation-design.md §2.1
 *
 * Visual contract:
 *   • 4px status-color left bar + property name + numeric value +
 *     unit + confidence chip.
 *   • `value_expression` renders into a `.g1-formula` span (the
 *     container class from globals.css — KaTeX rendering is layered
 *     on top by the formula wrapper, AC-5).
 *   • `validity_check.status === "fail"` triggers the
 *     `.g1-row-invalid` class so the red-row + 500ms shake fires
 *     (AC-10).
 *
 * Pure & immutable — receives the row by value, never mutates.
 */

import type { CSSProperties, ReactNode } from "react"
import katex from "katex"
import "katex/dist/katex.min.css"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"

export interface MeasurementRowItemProps {
  readonly row: LiteratureExtractionResultItem
  readonly isSelected: boolean
  readonly onClick: () => void
}

interface ValidityCheck {
  readonly status: "ok" | "warn" | "fail" | "unknown"
  readonly reason: string | null
}

const REVIEW_STATUS_BAR_COLOR: Record<string, string> = {
  pending: "var(--review-status-pending)",
  confirmed: "var(--review-status-confirmed)",
  modified: "var(--review-status-modified)",
  invalid: "var(--review-status-invalid)",
  disputed: "var(--review-status-disputed)",
  skipped: "var(--review-status-skipped)",
}

function readValidity(row: LiteratureExtractionResultItem): ValidityCheck {
  // The data shape mirrors the §3.1 contract: validity_check is a JSONB
  // block carried under `item_data`. Pre-G1-D rows may not have it,
  // in which case the dormant "unknown" status lights up no styling.
  const raw = row.item_data?.["validity_check"]
  if (raw == null || typeof raw !== "object") {
    return { status: "unknown", reason: null }
  }
  const obj = raw as Record<string, unknown>
  const status =
    obj["status"] === "ok" ||
    obj["status"] === "warn" ||
    obj["status"] === "fail"
      ? obj["status"]
      : "unknown"
  const reason =
    typeof obj["reason"] === "string" && obj["reason"].length > 0
      ? obj["reason"]
      : null
  return { status, reason }
}

function readValueExpression(row: LiteratureExtractionResultItem): string | null {
  const raw = row.item_data?.["value_expression"]
  return typeof raw === "string" && raw.length > 0 ? raw : null
}

function confidenceTone(confidence: number | null | undefined): "green" | "orange" | "red" {
  if (confidence == null) return "orange"
  if (confidence < 0.7) return "red"
  if (confidence < 0.85) return "orange"
  return "green"
}

function formatValue(value: unknown): string {
  if (value == null) return "—"
  if (typeof value === "number") return String(value)
  if (typeof value === "string") return value
  if (typeof value === "boolean") return value ? "true" : "false"
  return JSON.stringify(value)
}

/** Escape HTML-significant characters. `&` goes first so the entities
 * this function introduces are never themselves re-escaped. Exported
 * for the unit test pinning the KaTeX-fallback hardening. */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function renderFormula(expr: string): string {
  // AC-5 — `value_expression` is rendered through KaTeX so the
  // canonical LaTeX (e.g. "\\frac{k}{T}", "\\rho") appears in math
  // form rather than as raw backslashes.
  //
  // Security: `trust: false` disables \href and \includegraphics,
  // closing the two injection vectors documented in
  // docs/design/nfm-4552-g1-review-ux-design-spec.md addendum (3).
  // `throwOnError: false` keeps a malformed expression from blowing
  // up the sidebar — the user still sees the source with a red
  // border via the `.katex-error` class.
  try {
    return katex.renderToString(expr, {
      throwOnError: false,
      trust: false,
      output: "html",
    })
  } catch {
    // Defensive belt-and-braces; throwOnError:false already catches
    // the common cases. The return value flows into
    // dangerouslySetInnerHTML, so the raw source must be escaped —
    // a non-parse exception can never smuggle markup through here.
    return escapeHtml(expr)
  }
}

export function MeasurementRowItem({ row, isSelected, onClick }: MeasurementRowItemProps): ReactNode {
  const validity = readValidity(row)
  const valueExpression = readValueExpression(row)
  const isInvalid = validity.status === "fail"
  const reviewStatus = row.review_status ?? "pending"
  const barColor = REVIEW_STATUS_BAR_COLOR[reviewStatus] ?? "var(--review-status-pending)"

  const rowStyle: CSSProperties = {
    cursor: "pointer",
    padding: "10px 12px 10px 16px",
    // NFM-4576 W1: on invalid rows the border + background defer to the
    // `.g1-row-invalid` class rules in globals.css — inline declarations
    // would win the cascade and bury the red-row treatment (AC-10).
    // Selection feedback on an invalid row stays available via
    // aria-pressed + the open drawer; the louder invalid signal wins
    // the paint.
    borderLeft: isInvalid ? undefined : `4px solid ${barColor}`,
    backgroundColor: isInvalid
      ? undefined
      : isSelected
        ? "rgba(147, 197, 253, 0.08)"
        : "transparent",
    transition: "background-color 150ms var(--onto-ease-out, ease-out)",
  }

  return (
    <div
      data-testid={`measurement-row-${row.id}`}
      data-row="true"
      data-row-id={row.id}
      className={isInvalid ? "g1-row-invalid" : undefined}
      title={isInvalid && validity.reason ? validity.reason : undefined}
      style={rowStyle}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onClick()
        }
      }}
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 4,
        }}
      >
        <span style={{ fontWeight: 500, color: "#e5e7eb", fontSize: 14 }}>
          {row.property_name}
        </span>
        <span
          aria-label="置信度"
          style={{
            fontSize: 11,
            padding: "1px 6px",
            borderRadius: 4,
            background:
              confidenceTone(row.confidence) === "green"
                ? "rgba(16, 185, 129, 0.15)"
                : confidenceTone(row.confidence) === "orange"
                  ? "rgba(245, 158, 11, 0.18)"
                  : "rgba(239, 68, 68, 0.18)",
            color:
              confidenceTone(row.confidence) === "green"
                ? "#10b981"
                : confidenceTone(row.confidence) === "orange"
                  ? "#f59e0b"
                  : "#ef4444",
          }}
        >
          {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : "—"}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 8,
          fontSize: 15,
          color: isInvalid ? "var(--alert-error-text-strong, #fee2e2)" : "#cbd5e1",
        }}
      >
        {valueExpression ? (
          <span
            className="g1-formula"
            data-testid={`formula-${row.id}`}
            // AC-5 — value_expression is rendered through KaTeX. The
            // HTML payload is trusted: the expression originates from
            // server-side property_measurements.value_expression (set
            // by the extraction pipeline, not the end user). KaTeX
            // is configured with trust:false + throwOnError:false so
            // the two injection vectors (\href / \includegraphics)
            // are disabled and malformed expressions degrade to the
            // raw source with a .katex-error class.
            dangerouslySetInnerHTML={{ __html: renderFormula(valueExpression) }}
          />
        ) : (
          <span className="g1-numeric" style={{ fontSize: 15 }}>
            {formatValue(row.value)}
          </span>
        )}
        {row.unit && (
          <span
            className="g1-numeric"
            style={{ color: "#94a3b8", fontSize: 13 }}
            data-testid={`unit-${row.id}`}
          >
            {row.unit}
          </span>
        )}
      </div>
      {isInvalid && validity.reason && (
        <div
          style={{
            marginTop: 6,
            fontSize: "var(--fs-error-reason, 0.8125rem)",
            color: "var(--alert-error-text, #fecaca)",
          }}
        >
          ⚠ {validity.reason}
        </div>
      )}
    </div>
  )
}
