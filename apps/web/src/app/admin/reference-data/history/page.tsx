/** Fill History admin page (NFM-3750).

Displays all staging records with chronological history.
Filters: element_system, phase, property_name, confidence, status.
Read-only view for audit trail.

Uses cursor-based pagination for stable navigation over large datasets.

Next.js 16 prerender requirement: any client component that calls
useSearchParams() must be wrapped in a <Suspense> boundary at the page
level, otherwise static export fails with "missing-suspense-with-csr-bailout".
The actual logic lives in <FillHistoryPageContent /> (a client component
that uses useSearchParams via useCursorPagination); the page itself is a
server component that wraps it in Suspense so prerender succeeds.
*/

import { Suspense } from react
import { Skeleton } from antd
import { FillHistoryPageContent } from ./fill-history-page-content

export const dynamic = force-dynamic

export default function FillHistoryPage() {
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 8 }} />}>
      <FillHistoryPageContent />
    </Suspense>
  )
}
