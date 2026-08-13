/** Review Actions component.

Per-row approve/reject actions with:
- Approve: Popconfirm with optional note
- Reject: Modal with required reason
*/

"use client"

import { useState } from "react"
import { Button, Space, Modal, Input, Popconfirm, message } from "antd"
import { CheckOutlined, CloseOutlined } from "@ant-design/icons"
import type { StagingRecord } from "@/lib/admin/reference-data-types"

interface ReviewActionsProps {
  record: StagingRecord
  onApprove: (recordId: string, note?: string) => Promise<void>
  onReject: (recordId: string, reason: string) => Promise<void>
}

export function ReviewActions({ record, onApprove, onReject }: ReviewActionsProps) {
  const [approveModalVisible, setApproveModalVisible] = useState(false)
  const [approveNote, setApproveNote] = useState("")
  const [rejectModalVisible, setRejectModalVisible] = useState(false)
  const [rejectReason, setRejectReason] = useState("")
  const [actionLoading, setActionLoading] = useState(false)

  const handleApproveClick = () => {
    setApproveModalVisible(true)
  }

  const handleApproveConfirm = async () => {
    setActionLoading(true)
    try {
      await onApprove(record.id, approveNote || undefined)
      setApproveModalVisible(false)
      setApproveNote("")
    } catch {
      // Error already handled in parent
    } finally {
      setActionLoading(false)
    }
  }

  const handleRejectClick = () => {
    setRejectModalVisible(true)
  }

  const handleRejectConfirm = async () => {
    if (!rejectReason.trim()) {
      message.warning("请输入拒绝原因")
      return
    }

    setActionLoading(true)
    try {
      await onReject(record.id, rejectReason)
      setRejectModalVisible(false)
      setRejectReason("")
    } catch {
      // Error already handled in parent
    } finally {
      setActionLoading(false)
    }
  }

  const handleApproveCancel = () => {
    setApproveModalVisible(false)
    setApproveNote("")
  }

  const handleRejectCancel = () => {
    setRejectModalVisible(false)
    setRejectReason("")
  }

  return (
    <Space size="small">
      {/* Simple approve with Popconfirm for no-note case */}
      <Popconfirm
        title="确定要批准这条记录吗？"
        onConfirm={async () => {
          setActionLoading(true)
          try {
            await onApprove(record.id)
          } catch {
            // Error already handled in parent
          } finally {
            setActionLoading(false)
          }
        }}
        okText="确定"
        cancelText="取消"
      >
        <Button
          type="link"
          size="small"
          icon={<CheckOutlined />}
          loading={actionLoading}
        >
          批准
        </Button>
      </Popconfirm>

      {/* Approve with optional note */}
      <Button
        type="link"
        size="small"
        icon={<CheckOutlined />}
        onClick={handleApproveClick}
        loading={actionLoading}
      >
        批准(备注)
      </Button>

      {/* Reject with required reason */}
      <Button
        type="link"
        size="small"
        danger
        icon={<CloseOutlined />}
        onClick={handleRejectClick}
        loading={actionLoading}
      >
        拒绝
      </Button>

      {/* Approve with Note Modal */}
      <Modal
        title={`批准记录 - ${record.element_system} ${record.property_name}`}
        open={approveModalVisible}
        onOk={handleApproveConfirm}
        onCancel={handleApproveCancel}
        okText="确认批准"
        cancelText="取消"
        confirmLoading={actionLoading}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <p>
            <strong>元素系统:</strong> {record.element_system}
          </p>
          <p>
            <strong>相:</strong> {record.phase || "-"}
          </p>
          <p>
            <strong>属性:</strong> {record.property_name}
          </p>
          <p>
            <strong>数值:</strong> {record.value} {record.unit}
          </p>
          <p>
            <strong>来源:</strong> {record.source}
          </p>
          <Input.TextArea
            rows={4}
            placeholder="可选：添加批准备注（2000字符以内）"
            value={approveNote}
            onChange={(e) => setApproveNote(e.target.value)}
            maxLength={2000}
            showCount
          />
        </Space>
      </Modal>

      {/* Reject Modal */}
      <Modal
        title={`拒绝记录 - ${record.element_system} ${record.property_name}`}
        open={rejectModalVisible}
        onOk={handleRejectConfirm}
        onCancel={handleRejectCancel}
        okText="确认拒绝"
        cancelText="取消"
        confirmLoading={actionLoading}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <p>
            <strong>元素系统:</strong> {record.element_system}
          </p>
          <p>
            <strong>相:</strong> {record.phase || "-"}
          </p>
          <p>
            <strong>属性:</strong> {record.property_name}
          </p>
          <p>
            <strong>数值:</strong> {record.value} {record.unit}
          </p>
          <p>
            <strong>来源:</strong> {record.source}
          </p>
          <Input.TextArea
            rows={4}
            placeholder="请输入拒绝原因（必填，2000字符以内）"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            maxLength={2000}
            showCount
          />
        </Space>
      </Modal>
    </Space>
  )
}
