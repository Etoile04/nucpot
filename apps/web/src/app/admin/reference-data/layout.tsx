/** Admin layout for reference data management.

Provides a shared layout with sidebar navigation for:
- 审核队列
- 覆盖率看板
- 填充历史
*/

"use client"

import { Layout, Menu } from "antd"
import {
  EyeOutlined,
  DashboardOutlined,
  HistoryOutlined,
} from "@ant-design/icons"
import Link from "next/link"
import { usePathname } from "next/navigation"

const { Sider, Content } = Layout

export default function ReferenceDataLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()

  const menuItems = [
    {
      key: "/admin/reference-data/review",
      icon: <EyeOutlined />,
      label: <Link href="/admin/reference-data/review">审核队列</Link>,
    },
    {
      key: "/admin/reference-data/dashboard",
      icon: <DashboardOutlined />,
      label: <Link href="/admin/reference-data/dashboard">覆盖率看板</Link>,
    },
    {
      key: "/admin/reference-data/history",
      icon: <HistoryOutlined />,
      label: <Link href="/admin/reference-data/history">填充历史</Link>,
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
          参考数据管理
        </div>
        <Menu
          mode="inline"
          selectedKeys={[pathname]}
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
