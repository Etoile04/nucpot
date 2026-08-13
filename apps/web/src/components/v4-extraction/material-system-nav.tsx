/**
 * MaterialSystemNav -- left sidebar navigation for material system browsing.
 *
 * Displays a searchable list of material systems with property counts
 * and pending review badges.
 */

"use client"

import { Input, Menu, Badge, Typography } from "antd"
import { ExperimentOutlined } from "@ant-design/icons"
import { useMemo, useState } from "react"
import type { V4MaterialSystemSummary } from "@/lib/v4-extraction/types"

const { Search } = Input
const { Text } = Typography

// ─── Props ──────────────────────────────────────────────────────────

interface MaterialSystemNavProps {
  systems: V4MaterialSystemSummary[]
  selectedKey: string
  onSelect: (name: string) => void
}

// ─── Component ─────────────────────────────────────────────────────

export default function MaterialSystemNav({
  systems,
  selectedKey,
  onSelect,
}: MaterialSystemNavProps) {
  const [searchText, setSearchText] = useState("")

  const filteredSystems = useMemo(() => {
    if (!searchText.trim()) return systems
    const lower = searchText.toLowerCase()
    return systems.filter(
      (sys) =>
        sys.name.toLowerCase().includes(lower) ||
        sys.display_name.toLowerCase().includes(lower),
    )
  }, [systems, searchText])

  const menuItems = useMemo(
    () =>
      filteredSystems.map((sys) => ({
        key: sys.name,
        icon: <ExperimentOutlined />,
        label: (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              width: "100%",
              gap: 8,
            }}
          >
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
              {sys.display_name}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {sys.total_properties}
              </Text>
              {sys.pending_review_count > 0 && (
                <Badge count={sys.pending_review_count} size="small" />
              )}
            </div>
          </div>
        ),
      })),
    [filteredSystems],
  )

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "12px 12px 8px 12px" }}>
        <Search
          placeholder="搜索材料体系..."
          allowClear
          size="small"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
        />
      </div>
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        onClick={({ key }) => onSelect(key)}
        style={{
          flex: 1,
          overflow: "auto",
          borderRight: 0,
        }}
        items={menuItems}
      />
    </div>
  )
}
