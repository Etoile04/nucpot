import AuthGuard from "@/components/auth/AuthGuard"

export const metadata = {
  title: "Dashboard - NFMD",
  description: "Authenticated dashboard area",
}

/**
 * Dashboard layout — wraps all (dashboard)/* routes with JWT auth.
 * Route group (dashboard) doesn't affect URL path:
 *   (dashboard)/rag/chat → /rag/chat
 *   (dashboard)/review/kg → /review/kg
 *
 * Spec: NFM-826 §2.3 — middleware guard for (dashboard) route group.
 * Uses AuthGuard component for client-side JWT validation.
 * Next.js middleware.ts provides Edge-level pre-flight check.
 */
export default function DashboardLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return (
    <AuthGuard>
      <div className="min-h-[calc(100vh-73px)] bg-gray-900">
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </AuthGuard>
  )
}
