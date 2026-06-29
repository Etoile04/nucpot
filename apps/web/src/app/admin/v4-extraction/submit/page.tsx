"use client"

import { Typography, Row, Col, Card, Divider } from "antd"
import { SubmitForm } from "@/components/v4-extraction/submit-form"
import { RecentJobsTable } from "@/components/v4-extraction/recent-jobs-table"

export default function V4ExtractionSubmitPage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={4}>
        提交提取任务 / Submit Extraction Job
      </Typography.Title>

      <Row justify="center">
        <Col xs={24} sm={20} md={16} lg={14}>
          <Card>
            <SubmitForm />
          </Card>
        </Col>
      </Row>

      <Divider />

      <Card title="最近提交 / Recent Submissions">
        <RecentJobsTable />
      </Card>
    </div>
  )
}
