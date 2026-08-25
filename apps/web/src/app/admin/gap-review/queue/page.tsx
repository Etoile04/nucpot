import { Suspense } from 'react'
import { Spin } from 'antd'
import { GapReviewQueuePage } from '@/components/gap-review/GapReviewQueuePage'

export const dynamic = 'force-dynamic'

export default function GapReviewQueueRoute() {
  return (
    <Suspense fallback={<Spin size="large" style={{ display: 'block', margin: '100px auto' }} />}>
      <GapReviewQueuePage />
    </Suspense>
  )
}
