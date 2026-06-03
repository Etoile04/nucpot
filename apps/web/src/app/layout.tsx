import type { Metadata } from "next"
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
      <body>{children}</body>
    </html>
  )
}
