import type { Metadata } from "next"
import { AntdProvider } from "@/components/antd-provider"
import { QueryProvider } from "@/components/query-provider"
import { FeedbackFloatButton } from "@/components/feedback"
import Footer from "@/components/Footer"
import Nav from "@/components/Nav"
import AuthProvider from "@/components/AuthProvider"
import { SessionProvider } from "@/components/session"
import "@/styles/globals.css"

export const metadata: Metadata = {
  title: "NucPot — 核材料势函数库",
  description:
    "面向核燃料、包壳和结构材料的原子间势函数开放平台。覆盖 EAM、MEAM、机器学习势等多种形式，支持 LAMMPS 等主流模拟软件。",
}

/**
 * Root layout — mounts SessionProvider at the app root so that
 * <SessionIndicator /> inside <Nav /> can read the session context
 * on ALL routes (not just (dashboard) routes).
 *
 * NFM-2255 fix: SessionProvider was previously only in (dashboard)/layout.tsx,
 * which made it invisible to Nav (root-level sibling).  Moving it here
 * resolves the context-hierarchy bug.  The provider gracefully returns
 * "unauthenticated" when no session cookie exists, so public routes
 * (/login, /browse) are unaffected — SessionIndicator simply hides.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      {/* suppressHydrationWarning: Ant Design CSS-in-JS + FloatButton portal
          cause minor DOM differences between SSR and client hydration.
          This is the standard fix recommended by both Next.js and Ant Design docs
          for App Router + antd v5. See NFM-3309 W12. */}
      <body className="h-screen flex flex-col overflow-hidden bg-gray-900 text-white" suppressHydrationWarning>
        <AntdProvider>
          <QueryProvider>
            <AuthProvider>
            <SessionProvider>
              <Nav />
              <main className="flex-1 overflow-y-auto">{children}</main>
              <Footer />
              <FeedbackFloatButton />
            </SessionProvider>
            </AuthProvider>
          </QueryProvider>
        </AntdProvider>
      </body>
    </html>
  )
}
