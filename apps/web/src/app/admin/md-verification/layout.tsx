/** Admin layout for MD verification management.

Provides a shared layout with sidebar navigation for:
- 任务提交与列表
- 任务详情 (handled dynamically)
*/

"use client"

import { Layout, Menu } from "antd"
import { ExperimentOutlined, UnorderedListOutlined } from "@ant-design/icons"
import Link from "next/link"
import { usePathname } from "next/navigation"

const { Sider, Content } = Layout

export default function MDVerificationLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()

  const menuItems = [
    {
      key: "/admin/md-verification",
      icon: <ExperimentOutlined />,
      label: <Link href="/admin/md-verification">MD 验证</Link>,
    },
    {
      key: "/admin/md-verification/list",
      icon: <UnorderedListOutlined />,
      label: <Link href="/admin/md-verification#list">任务列表</Link>,
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
          MD 验证管理
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
