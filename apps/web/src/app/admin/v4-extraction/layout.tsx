/** Admin layout for V4 Extraction management.

Provides a shared layout with sidebar navigation for:
- 提交任务 / Submit
- 数据浏览 / Browse
- 人工审核 / Validate (with pending review badge)
*/

"use client"

import { Badge, Layout, Menu, Space } from "antd"
import {
  DatabaseOutlined,
  SafetyOutlined,
  SendOutlined,
} from "@ant-design/icons"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { getMaterialSystems } from "@/lib/v4-extraction/api"

const { Sider, Content } = Layout

export default function V4ExtractionLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()

  // Normalize pathname for menu highlighting: /status/{jobId} → /status
  const normalizedPath = pathname.replace(/\/status\/.*/, "/status")
  const selectedKeys = [normalizedPath]

  // Poll for pending review count for the badge
  const { data: pendingSystems } = useQuery({
    queryKey: ["v4-material-systems-pending"],
    queryFn: () => getMaterialSystems({ has_pending_review: true }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  })

  const pendingReviewCount =
    pendingSystems?.reduce((sum, sys) => sum + sys.pending_review_count, 0) ??
    0

  const menuItems = [
    {
      key: "/admin/v4-extraction/submit",
      icon: <SendOutlined />,
      label: (
        <Link href="/admin/v4-extraction/submit">
          提交任务 / Submit
        </Link>
      ),
    },
    {
      key: "/admin/v4-extraction/browse",
      icon: <DatabaseOutlined />,
      label: (
        <Link href="/admin/v4-extraction/browse">
          数据浏览 / Browse
        </Link>
      ),
    },
    {
      key: "/admin/v4-extraction/validate",
      icon: <SafetyOutlined />,
      label: (
        <Link href="/admin/v4-extraction/validate">
          <Space size={4}>
            <span>人工审核 / Validate</span>
            {pendingReviewCount > 0 && (
              <Badge count={pendingReviewCount} size="small" />
            )}
          </Space>
        </Link>
      ),
    },
  ]

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={200}
        style={{
          background: "#fff",
          overflow: "auto",
          height: "100vh",
          position: "fixed",
          left: 0,
          top: 0,
          bottom: 0,
        }}
      >
        <div
          style={{
            height: "64px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderBottom: "1px solid #f0f0f0",
            fontWeight: "bold",
            fontSize: "16px",
          }}
        >
          V4 提取系统
        </div>
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          style={{ height: "100%", borderRight: 0 }}
          items={menuItems}
        />
      </Sider>
      <Layout style={{ marginLeft: 200 }}>
        <Content
          style={{
            background: "#fff",
            margin: 0,
            minHeight: "100vh",
          }}
        >
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
