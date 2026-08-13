/**
 * SidebarSkeleton -- animated placeholder rows for the material systems
 * sidebar. Replaces the indefinite text "加载中..." spinner.
 *
 * Each row mimics the height of a Menu item (32px) with Ant Design
 * skeleton pulse animation.
 */

"use client"

import { Skeleton } from "antd"

// ─── Props ──────────────────────────────────────────────────────────

interface SidebarSkeletonProps {
  rows?: number
}

// ─── Component ──────────────────────────────────────────────────────

export default function SidebarSkeleton({ rows = 8 }: SidebarSkeletonProps) {
  return (
    <div style={{ padding: "12px 12px 8px 12px" }}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton.Input
          key={i}
          active
          size="small"
          block
          style={{ marginBottom: 8, height: 32 }}
        />
      ))}
    </div>
  )
}
