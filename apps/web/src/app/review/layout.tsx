import ReviewAuthGuard from '@/components/review/ReviewAuthGuard'

export const metadata = {
  title: 'Review',
  description: 'Knowledge graph review and conflict resolution',
}

/**
 * Review layout — wraps all /review/* routes with JWT-based auth.
 * Uses ReviewAuthGuard which checks /api/v1/auth/me and redirects
 * unauthenticated users to /login.
 * Spec: NFM-1006
 */
export default function ReviewLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return (
    <ReviewAuthGuard>
      <div className="min-h-[calc(100vh-73px)] bg-gray-900">
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </ReviewAuthGuard>
  )
}
