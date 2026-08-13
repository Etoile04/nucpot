"use client"

import { Checkbox, Tag } from "antd"
import type { ConfigType } from "../types"
import { CONFIG_TYPES, ALL_CONFIG_TYPES } from "../constants"

interface ConfigTypeFilterProps {
  readonly selected: ConfigType[]
  readonly onChange: (types: ConfigType[]) => void
}

/**
 * Checkbox group for configuration type filtering (Type I-IV).
 * Each checkbox has a colored Tag suffix.
 */
export function ConfigTypeFilter({
  selected,
  onChange,
}: ConfigTypeFilterProps) {
  const options = ALL_CONFIG_TYPES.map((key) => {
    const meta = CONFIG_TYPES[key]
    return {
      label: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {meta.label}
          <Tag color={meta.color} style={{ margin: 0, fontSize: 11 }}>
            {key.replace("_", " ")}
          </Tag>
        </span>
      ),
      value: key,
    }
  })

  return (
    <Checkbox.Group
      value={selected}
      onChange={(values) => onChange(values as ConfigType[])}
      options={options}
    />
  )
}
