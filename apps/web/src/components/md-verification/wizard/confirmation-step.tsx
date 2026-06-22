"use client"

import { useState } from "react"
import {
  Descriptions,
  Button,
  Modal,
  Alert,
  Space,
  Tag,
  Typography,
  message,
} from "antd"
import {
  SendOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons"
import {
  submitMDVerificationJob,
  type MDVerificationJobSubmitRequest,
} from "@/lib/md-verification-api"
import {
  HPC_BACKEND_OPTIONS,
  DEFECT_TYPE_LABELS,
  type WizardFormData,
} from "./wizard-types"

const { Text } = Typography

interface ConfirmationStepProps {
  formData: WizardFormData
  onSuccess?: (jobId: string) => void
}

function getHpcBackendLabel(value: string): string {
  const option = HPC_BACKEND_OPTIONS.find((o) => o.value === value)
  return option?.label ?? value
}

function formatDefectTypes(types: readonly string[]): string {
  return types
    .map((t) => DEFECT_TYPE_LABELS[t] ?? t)
    .join("、")
}

function buildSubmitPayload(
  formData: WizardFormData,
): MDVerificationJobSubmitRequest {
  const potential = formData.selectedPotential
  if (!potential) {
    throw new Error("未选择势函数")
  }

  return {
    potential_id: potential.id,
    element_system: formData.elementSystem,
    phase: formData.phase || undefined,
    potential_file: potential.file_url ?? "",
    structure_file: formData.structureFile,
    config: {
      temperature: formData.temperature,
      pressure: formData.pressure,
      simulation_time: formData.simulationTime,
      timestep: formData.timestep,
      ensemble: formData.ensemble,
    },
    priority: formData.priority,
  }
}

export function ConfirmationStep({
  formData,
  onSuccess,
}: ConfirmationStepProps) {
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)

  const potential = formData.selectedPotential

  const handleSubmit = async () => {
    setLoading(true)
    try {
      const payload = buildSubmitPayload(formData)
      const result = await submitMDVerificationJob(payload)

      message.success(
        <span>
          任务提交成功！
          <br />
          任务ID: <code>{result.id}</code>
        </span>,
      )

      setModalVisible(false)
      onSuccess?.(result.id)
    } catch (error: unknown) {
      const errorMessage =
        error instanceof Error ? error.message : "提交失败"
      message.error(`任务提交失败: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }

  const isRemoteHpc =
    formData.hpcBackend !== "" && formData.hpcBackend !== "local"

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      {/* Read-only summary */}
      <Descriptions
        title="任务配置摘要"
        bordered
        size="small"
        column={{ xs: 1, sm: 1, md: 2 }}
      >
        <Descriptions.Item label="势函数" span={2}>
          <Space>
            <Text strong>{potential?.name ?? "未选择"}</Text>
            {potential?.type && (
              <Tag color="blue">{potential.type}</Tag>
            )}
            {potential?.elements.map((el) => (
              <Tag key={el}>{el}</Tag>
            ))}
          </Space>
        </Descriptions.Item>

        <Descriptions.Item label="元素体系">
          {formData.elementSystem}
        </Descriptions.Item>
        <Descriptions.Item label="相结构">
          {formData.phase || "未指定"}
        </Descriptions.Item>

        <Descriptions.Item label="温度">
          {formData.temperature} K
        </Descriptions.Item>
        <Descriptions.Item label="压力">
          {formData.pressure} GPa
        </Descriptions.Item>

        <Descriptions.Item label="系综类型">
          {formData.ensemble}
        </Descriptions.Item>
        <Descriptions.Item label="模拟时间">
          {formData.simulationTime} ps
        </Descriptions.Item>

        <Descriptions.Item label="时间步长">
          {formData.timestep} ps
        </Descriptions.Item>
        <Descriptions.Item label="缺陷类型">
          {formData.defectTypes.length > 0
            ? formatDefectTypes(formData.defectTypes)
            : "无"}
        </Descriptions.Item>

        <Descriptions.Item label="HPC 后端">
          {formData.hpcBackend
            ? getHpcBackendLabel(formData.hpcBackend)
            : "本地计算"}
        </Descriptions.Item>
        <Descriptions.Item label="优先级">
          {formData.priority}
        </Descriptions.Item>
      </Descriptions>

      {/* HPC budget warning */}
      {isRemoteHpc && (
        <Alert
          type="warning"
          showIcon
          message="HPC 算力消耗提醒"
          description="使用远程 HPC 集群将消耗项目算力配额。请确认参数配置正确后再提交。"
        />
      )}

      {/* Submit button */}
      <Button
        type="primary"
        size="large"
        block
        icon={<SendOutlined />}
        loading={loading}
        onClick={() => setModalVisible(true)}
      >
        提交任务
      </Button>

      {/* Confirmation modal */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: "#faad14" }} />
            <span>确认提交</span>
          </Space>
        }
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        okText="确认提交"
        cancelText="取消"
        confirmLoading={loading}
      >
        <p>请确认以下任务配置正确：</p>
        <ul style={{ paddingLeft: 20 }}>
          <li>
            势函数: {potential?.name ?? "未选择"}
          </li>
          <li>温度: {formData.temperature} K</li>
          <li>压力: {formData.pressure} GPa</li>
          <li>模拟时间: {formData.simulationTime} ps</li>
          <li>系综: {formData.ensemble}</li>
        </ul>
        {isRemoteHpc && (
          <Alert
            type="warning"
            message={`将提交至 ${getHpcBackendLabel(formData.hpcBackend)}，请确认算力预算充足。`}
            style={{ marginTop: 16 }}
          />
        )}
      </Modal>
    </Space>
  )
}
