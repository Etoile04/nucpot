"use client"

import { TaskSubmissionWizard } from "@/components/md-verification/task-submission-wizard"
import { TaskList } from "@/components/md-verification/task-list"
import { Tabs } from "antd"
import { useState } from "react"

export default function MDVerificationPage() {
  const [activeTab, setActiveTab] = useState("submit")
  const [refreshKey, setRefreshKey] = useState(0)

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const handleSubmissionSuccess = (_: string) => {
    // Switch to list view and refresh
    setActiveTab("list")
    setRefreshKey((prev) => prev + 1)
  }

  return (
    <div style={{ padding: "2rem", background: "#f5f5f5", minHeight: "100vh" }}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "submit",
            label: "提交任务",
            children: <TaskSubmissionWizard onSuccess={handleSubmissionSuccess} />,
          },
          {
            key: "list",
            label: "任务列表",
            children: <TaskList key={refreshKey} />,
          },
        ]}
      />
    </div>
  )
}
