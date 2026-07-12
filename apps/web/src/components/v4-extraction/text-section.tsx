"use client"

import { useState, useCallback } from "react"
import { Button, Empty } from "antd"
import { DownOutlined, UpOutlined } from "@ant-design/icons"

interface TextSectionProps {
  readonly text: string
  readonly maxHeight?: number
  readonly className?: string
}

export default function TextSection({
  text,
  maxHeight = 400,
  className,
}: TextSectionProps) {
  const [expanded, setExpanded] = useState(false)
  const needsTruncation = text.length > 1000

  const toggleExpand = useCallback(() => {
    setExpanded((prev) => !prev)
  }, [])

  if (!text) {
    return (
      <Empty
        description="无提取文本 / No extracted text"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }

  return (
    <div className={className}>
      <div
        style={{
          maxHeight: expanded ? "none" : `${maxHeight}px`,
          overflow: "hidden",
          position: "relative",
          whiteSpace: "pre-wrap",
          lineHeight: 1.8,
          fontSize: 14,
          color: "var(--color-text, #1f2937)",
          transition: "max-height 0.3s ease",
        }}
      >
        {text}
        {!expanded && needsTruncation && (
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: 60,
              background:
                "linear-gradient(transparent, var(--color-surface, #fff))",
              pointerEvents: "none",
            }}
          />
        )}
      </div>
      {needsTruncation && (
        <div style={{ textAlign: "center", marginTop: 8 }}>
          <Button
            type="link"
            size="small"
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={toggleExpand}
          >
            {expanded ? "收起 / Collapse" : "展开全部 / Expand All"}
          </Button>
        </div>
      )}
    </div>
  )
}
