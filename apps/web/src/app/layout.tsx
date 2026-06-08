import type { Metadata } from "next"
import { AntdProvider } from "@/components/antd-provider"
import { FeedbackFloatButton } from "@/components/feedback"
import "@/styles/globals.css"

export const metadata: Metadata = {
  title: "核燃料与材料物性数据库",
  description: "可持续共享的核燃料与材料物性数据库平台",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdProvider>
          {children}
          <footer
            style={{
              borderTop: "1px solid #f0f0f0",
              padding: "2rem 1.5rem",
              textAlign: "center",
              color: "#999",
              fontSize: "0.875rem",
              marginTop: "4rem",
            }}
          >
            <p>
              反馈与建议：
              <a
                href="mailto:feedback@nucpot.org"
                style={{ color: "var(--color-accent)" }}
              >
                feedback@nucpot.org
              </a>
            </p>
            <p>© {new Date().getFullYear()} 核燃料与材料物性数据库</p>
          </footer>
          <FeedbackFloatButton />
        </AntdProvider>
      </body>
    </html>
  )
}
