"use client"

import { Empty, Typography } from "antd"

const { Text } = Typography

/**
 * Compact table showing recently submitted extraction jobs.
 *
 * Placeholder implementation -- the backend does not yet have a list-jobs endpoint.
 * Renders an empty state until the endpoint is available.
 */
export function RecentJobsTable() {
  return (
    <Empty
      description={
        <Text type="secondary">
          暂无提交记录 / No recent submissions
        </Text>
      }
    />
  )
}
