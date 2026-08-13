"use client"

import Link from "next/link"
import { Typography, Space, Card, Row, Col, Button } from "antd"
import { BulbOutlined, SearchOutlined, AuditOutlined } from "@ant-design/icons"

const { Title, Text, Paragraph } = Typography

export default function AdminKgContent() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <Title level={2}>知识图谱管理</Title>
      <Paragraph type="secondary">
        知识图谱节点统计、审核队列管理和图谱浏览入口。
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card
            hoverable
            size="small"
            title={<><AuditOutlined /> 审核队列</>}
          >
            <Space direction="vertical" size="small">
              <Text type="secondary">查看待审核的 KG 实体，执行批准/拒绝操作</Text>
              <Link href="/review/kg">
                <Button type="link" size="small">前往审核队列 →</Button>
              </Link>
            </Space>
          </Card>
        </Col>

        <Col xs={24} sm={8}>
          <Card
            hoverable
            size="small"
            title={<><SearchOutlined /> 图谱浏览</>}
          >
            <Space direction="vertical" size="small">
              <Text type="secondary">浏览知识图谱节点和关系</Text>
              <Link href="/kg/search">
                <Button type="link" size="small">浏览图谱 →</Button>
              </Link>
            </Space>
          </Card>
        </Col>

        <Col xs={24} sm={8}>
          <Card
            hoverable
            size="small"
            title={<><BulbOutlined /> 冲突解决</>}
          >
            <Space direction="vertical" size="small">
              <Text type="secondary">处理多源数据融合中的实体冲突</Text>
              <Link href="/review/conflicts">
                <Button type="link" size="small">查看冲突 →</Button>
              </Link>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
