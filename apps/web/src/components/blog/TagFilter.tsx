"use client"

import { Tag, Space } from "antd"

interface TagFilterProps {
  readonly tags: readonly string[]
  readonly selectedTag: string | null
  readonly onSelectTag: (tag: string | null) => void
}

export function TagFilter({ tags, selectedTag, onSelectTag }: TagFilterProps) {
  const handleSelectAll = () => {
    onSelectTag(null)
  }

  const handleSelectTag = (tag: string) => {
    onSelectTag(tag)
  }

  return (
    <Space size={[8, 12]} wrap style={{ marginBottom: "1.5rem" }}>
      <Tag
        onClick={handleSelectAll}
        style={{
          cursor: "pointer",
          background: selectedTag === null ? "var(--color-accent)" : undefined,
          color: selectedTag === null ? "#ffffff" : undefined,
          borderColor: selectedTag === null ? "var(--color-accent)" : undefined,
        }}
      >
        全部
      </Tag>
      {tags.map((tag) => (
        <Tag
          key={tag}
          onClick={() => handleSelectTag(tag)}
          style={{
            cursor: "pointer",
            background: selectedTag === tag ? "var(--color-accent)" : undefined,
            color: selectedTag === tag ? "#ffffff" : undefined,
            borderColor: selectedTag === tag ? "var(--color-accent)" : undefined,
          }}
        >
          {tag}
        </Tag>
      ))}
    </Space>
  )
}
