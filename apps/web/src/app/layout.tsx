import type { Metadata } from "next"
import { AntdProvider } from "@/components/antd-provider"
import { QueryProvider } from "@/components/query-provider"
import { FeedbackFloatButton } from "@/components/feedback"
import Nav from "@/components/Nav"
import AuthProvider from "@/components/AuthProvider"
import "@/styles/globals.css"

export const metadata: Metadata = {
  title: "NucPot — 核材料势函数库",
  description:
    "面向核燃料、包壳和结构材料的原子间势函数开放平台。覆盖 EAM、MEAM、机器学习势等多种形式，支持 LAMMPS 等主流模拟软件。",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="h-screen flex flex-col overflow-hidden bg-gray-900 text-white">
        <AntdProvider>
          <QueryProvider>
            <AuthProvider>
            <Nav />
            <main className="flex-1 overflow-y-auto">{children}</main>
            <footer className="border-t border-gray-700 py-8 text-center text-gray-400 text-sm">
              <p>
                反馈与建议：
                <a
                  href="mailto:feedback@nucpot.org"
                  className="text-blue-400 hover:text-blue-300"
                >
                  feedback@nucpot.org
                </a>
              </p>
              <p>© {new Date().getFullYear()} 核燃料与材料物性数据库</p>
            </footer>
            <FeedbackFloatButton />
            </AuthProvider>
          </QueryProvider>
        </AntdProvider>
      </body>
    </html>
  )
}
