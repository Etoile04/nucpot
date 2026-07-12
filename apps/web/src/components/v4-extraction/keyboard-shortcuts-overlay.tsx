/**
 * KeyboardShortcutsOverlay -- fixed bottom bar showing available shortcuts.
 *
 * Displays muted, monospace shortcut hints at the bottom of the viewport.
 */

"use client"

import { Typography } from "antd"

// ─── Component ────────────────────────────────────────────────────

export default function KeyboardShortcutsOverlay() {
  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 200,
        right: 0,
        zIndex: 100,
        background: "rgba(0, 0, 0, 0.75)",
        padding: "6px 24px",
        display: "flex",
        justifyContent: "center",
      }}
    >
      <Typography.Text
        code
        style={{
          color: "rgba(255, 255, 255, 0.65)",
          fontFamily: "monospace",
          fontSize: 12,
          background: "transparent",
          border: "none",
        }}
      >
        [A] 批准&nbsp;&nbsp;[R] 拒绝&nbsp;&nbsp;[M] 修改&nbsp;&nbsp;[S/→]
        跳过&nbsp;&nbsp;[←] 返回
      </Typography.Text>
    </div>
  )
}
