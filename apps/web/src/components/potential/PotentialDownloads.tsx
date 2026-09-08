"use client"

import { Card, Empty, Space, Typography } from "antd"
import { FileOutlined } from "@ant-design/icons"
import type { PotentialDetail } from "@/lib/potentials-api"
import { FileLink } from "./FileLink"

const { Text } = Typography

interface PotentialDownloadsProps {
  readonly detail: PotentialDetail
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export function PotentialDownloads({ detail }: PotentialDownloadsProps) {
  const { file_url, file_size, file_hash, format, source } = detail

  if (!file_url) {
    return (
      <Empty description="暂无可下载文件，请从原始来源获取">
        {source && <Text type="secondary">数据来源：{source}</Text>}
      </Empty>
    )
  }

  // NFM-4458: <FileLink> is the single canonical download surface. It
  // trusts the BFF's canonical proxy URL, derives the browser
  // `download=` name from `extra.file_storage`, and renders a
  // "文件缺失" text node if the proxy URL is ever empty.
  return (
    <Card title="文件下载">
      <Space direction="vertical" size="middle" className="w-full">
        <Space align="center" size="middle">
          <FileOutlined style={{ fontSize: 24 }} />
          <div>
            <div>
              <Text strong>
                <FileLink variant="link" potential={detail} />
              </Text>
            </div>
            <Text type="secondary">
              {file_size != null ? formatSize(file_size) : "大小未知"}
              {format ? ` · ${format}` : ""}
            </Text>
          </div>
          <FileLink variant="button" potential={detail} />
        </Space>

        {file_hash && (
          <div>
            <Text type="secondary">SHA256：</Text>
            <Text code style={{ wordBreak: "break-all" }}>
              {file_hash}
            </Text>
          </div>
        )}

        {source && <Text type="secondary">数据来源：{source}</Text>}
      </Space>
    </Card>
  )
}
