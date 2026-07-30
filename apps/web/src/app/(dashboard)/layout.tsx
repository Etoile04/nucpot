import AuthGuard from "@/components/auth/AuthGuard"
import { ReAuthPrompt, SessionProvider } from "@/components/session"

export const metadata = {
  title: "Dashboard - NFMD",
  description: "Authenticated dashboard area",
}

/**
 * Dashboard layout — wraps all (dashboard)/* routes with JWT auth and
 * mounts the session-management surfaces for NFM-2236.
 *
 * Spec: NFM-826 §2.3 — middleware guard for (dashboard) route group.
 * Uses AuthGuard component for client-side JWT validation.
 * Next.js middleware.ts provides Edge-level pre-flight check.
 *
 * NFM-2254 — Mount points:
 *   - <SessionProvider> wraps the gated area so the refresh
 *     interceptor (NFM-2252) can drive the SessionManager that
 *     <ReAuthPrompt /> reads. The provider bootstraps via
 *     ``GET /auth/session`` exactly once per mount and tears down
 *     when the user navigates out of the (dashboard) group.
 *   - <ReAuthPrompt /> is rendered INSIDE <AuthGuard /> because the
 *     re-auth prompt is only meaningful for authenticated sessions:
 *     an unauthenticated visitor sees the AuthGuard's redirect
 *     first, and never the modal.
 *
 * NFM-2253's <SessionIndicator /> is mounted separately in Nav (root
 * layout) so it can sit in the header on /, /login, and (dashboard)
 * routes; the same SessionProvider mounted here is consumed by both.
 */
export default function DashboardLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return (
    <SessionProvider>
      <AuthGuard>
        <ReAuthPrompt />
        <div className="min-h-[calc(100vh-73px)] bg-gray-900">
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
            {children}
          </main>
        </div>
      </AuthGuard>
    </SessionProvider>
  )
}
