import BlogAuthGuard from '@/components/admin/BlogAuthGuard'

export const metadata = {
  title: 'Review',
  description: 'Knowledge graph review and conflict resolution',
}

/**
 * Review layout — wraps all /review/* routes with JWT-based auth.
 * Uses BlogAuthGuard which checks /api/v1/auth/me and redirects
 * unauthenticated users to /admin/login per NFM-848 spec.
 * When NFM-834.2 delivers the generalized AuthGuard, swap it in —
 * the wrapper pattern is identical.
 */
export default function ReviewLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return (
    <BlogAuthGuard>
      <div className="min-h-[calc(100vh-73px)] bg-gray-900">
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </BlogAuthGuard>
  )
}
