/** Admin layout for blog management.

Provides a shared layout with sidebar navigation for:
- 文章列表
- 审核队列
- 新建文章
*/

"use client"

import { Layout, Menu } from "antd"
import { CheckCircleOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { ReviewBadge } from "./components/ReviewBadge"

const { Sider, Content } = Layout

export default function BlogLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()

  const menuItems = [
    {
      key: "/admin/blog/posts",
      icon: <FileTextOutlined />,
      label: <Link href="/admin/blog/posts">文章列表</Link>,
    },
    {
      key: "/admin/blog/review",
      icon: <CheckCircleOutlined />,
      label: (
        <span style={{ display: "inline-flex", alignItems: "center" }}>
          <Link href="/admin/blog/review" style={{ display: "inline" }}>审核队列</Link>
          <ReviewBadge />
        </span>
      ),
    },
    {
      key: "/admin/blog/new",
      icon: <PlusOutlined />,
      label: <Link href="/admin/blog/new">新建文章</Link>,
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
          博客管理
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
