"use client"

import { Card, Button, Empty, Space, Typography } from "antd"
import { DownloadOutlined, FileOutlined } from "@ant-design/icons"
import Link from "next/link"
import type { PotentialDetail } from "@/lib/potentials-api"

const { Text } = Typography

interface PotentialDownloadsProps {
  readonly detail: PotentialDetail
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function resolveFileUrl(fileUrl: string): string {
  // file_url is a relative path under /uploads/ (e.g., "/uploads/foo.eam.alloy")
  return fileUrl.startsWith("/") ? fileUrl : `/uploads/${fileUrl}`
}

function fileNameFromUrl(fileUrl: string): string {
  const cleaned = fileUrl.split("?")[0]?.split("#")[0] ?? fileUrl
  const segments = cleaned.split("/")
  return segments[segments.length - 1] || fileUrl
}

export function PotentialDownloads({ detail }: PotentialDownloadsProps) {
  const { file_url, file_size, file_hash, format, source } = detail

  if (!file_url) {
    return (
      <Empty description="暂无可下载文件，请从原始来源获取">
        {source && (
          <Text type="secondary">
            数据来源：{source}
          </Text>
        )}
      </Empty>
    )
  }

  const url = resolveFileUrl(file_url)
  const fileName = fileNameFromUrl(file_url)

  return (
    <Card title="文件下载">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Space align="center" size="middle">
          <FileOutlined style={{ fontSize: 24 }} />
          <div>
            <div>
              <Text strong>{fileName}</Text>
            </div>
            <Text type="secondary">
              {file_size != null ? formatSize(file_size) : "大小未知"}
              {format ? ` · ${format}` : ""}
            </Text>
          </div>
          <Link href={url} download={fileName}>
            <Button type="primary" icon={<DownloadOutlined />}>
              下载
            </Button>
          </Link>
        </Space>

        {file_hash && (
          <div>
            <Text type="secondary">SHA256：</Text>
            <Text code style={{ wordBreak: "break-all" }}>
              {file_hash}
            </Text>
          </div>
        )}

        {source && (
          <Text type="secondary">数据来源：{source}</Text>
        )}
      </Space>
    </Card>
  )
}
