import AuthGuard from "@/components/auth/AuthGuard"
import { ReAuthPrompt } from "@/components/session"

export const metadata = {
  title: "Dashboard - NFMD",
  description: "Authenticated dashboard area",
}

/**
 * Dashboard layout — wraps all (dashboard)/* routes with JWT auth and
 * mounts the re-auth prompt surface for NFM-2236.
 *
 * Spec: NFM-826 §2.3 — middleware guard for (dashboard) route group.
 * Uses AuthGuard component for client-side JWT validation.
 * Next.js middleware.ts provides Edge-level pre-flight check.
 *
 * NFM-2254 — <ReAuthPrompt /> is rendered INSIDE <AuthGuard /> because
 * the re-auth prompt is only meaningful for authenticated sessions:
 * an unauthenticated visitor sees the AuthGuard's redirect first,
 * and never the modal.
 *
 * NFM-2255 fix: <SessionProvider> was moved to root layout.tsx so that
 * <SessionIndicator /> in Nav can read the session context on all routes.
 * This layout now only adds AuthGuard + ReAuthPrompt for the dashboard area.
 */
export default function DashboardLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return (
    <AuthGuard>
      <ReAuthPrompt />
      <div className="min-h-[calc(100vh-73px)] bg-gray-900">
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </AuthGuard>
  )
}
