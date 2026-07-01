"use client"

import { useEffect, useState, useCallback } from "react"
import { Layout, Menu } from "antd"
import { CheckCircleOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import BlogAuthGuard from "@/components/admin/BlogAuthGuard"
import {
  authApi,
  blogApi,
  type UserProfile,
} from "@/lib/api-client"

const { Sider, Content } = Layout

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  editor: "编辑",
  reviewer: "审核",
}

function useReviewCount(): number {
  const [count, setCount] = useState(0)

  useEffect(() => {
    blogApi
      .list({ status: "under_review", limit: 1 })
      .then((posts) => setCount(posts.length))
      .catch(() => setCount(0))
  }, [usePathname()])

  return count
}

export default function BlogLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const reviewCount = useReviewCount()

  useEffect(() => {
    authApi
      .getMe()
      .then(setProfile)
      .catch(() => setProfile(null))
  }, [])

  const handleLogout = useCallback(() => {
    authApi.logout()
    router.replace("/admin/login")
  }, [router])

  const badgeText =
    reviewCount > 0 ? (reviewCount > 9 ? "9+" : String(reviewCount)) : null

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
          <Link href="/admin/blog/review">审核队列</Link>
          {badgeText && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: "16px",
                height: "16px",
                padding: "0 4px",
                fontSize: "0.6875rem",
                lineHeight: "16px",
                borderRadius: "8px",
                background: "#ff4d4f",
                color: "#fff",
                fontWeight: 600,
                marginLeft: "0.5rem",
              }}
            >
              {badgeText}
            </span>
          )}
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
    <BlogAuthGuard>
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
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              borderBottom: "1px solid #f0f0f0",
            }}
          >
            <span style={{ fontWeight: "bold", fontSize: "16px" }}>
              博客管理
            </span>
            {profile?.blog_role && (
              <span
                style={{
                  fontSize: "0.75rem",
                  padding: "0.125rem 0.375rem",
                  borderRadius: 2,
                  background: "#e6f7ff",
                  color: "#1890ff",
                  border: "1px solid #91d5ff",
                  marginTop: "0.25rem",
                }}
              >
                {ROLE_LABELS[profile.blog_role] ?? profile.blog_role}
              </span>
            )}
          </div>
          <Menu
            mode="inline"
            selectedKeys={[pathname]}
            style={{ height: "100%", borderRight: 0 }}
            items={menuItems}
          />
          <div
            style={{
              position: "absolute",
              bottom: 0,
              width: "100%",
              borderTop: "1px solid #f0f0f0",
              padding: "0.75rem 1rem",
            }}
          >
            <a
              onClick={handleLogout}
              style={{
                fontSize: "0.875rem",
                color: "#666",
                textDecoration: "none",
                cursor: "pointer",
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleLogout()
              }}
              role="button"
              tabIndex={0}
            >
              退出登录
            </a>
          </div>
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
    </BlogAuthGuard>
  )
}
