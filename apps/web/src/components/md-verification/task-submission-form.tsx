"use client"

import { useState } from "react"
import { Form, Input, InputNumber, Select, Button, message, Card, Space } from "antd"
import { SendOutlined } from "@ant-design/icons"
import {
  submitMDVerificationJob,
  type MDVerificationJobSubmitRequest,
} from "@/lib/md-verification-api"

const { Option } = Select

interface TaskSubmissionFormProps {
  onSuccess?: (jobId: string) => void
}

export function TaskSubmissionForm({ onSuccess }: TaskSubmissionFormProps) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (values: MDVerificationJobSubmitRequest) => {
    setLoading(true)
    try {
      // Validate file paths
      if (!values.potential_file || values.potential_file.trim() === "") {
        message.error("请输入势函数文件路径")
        return
      }

      if (!values.structure_file || values.structure_file.trim() === "") {
        message.error("请输入结构文件路径")
        return
      }

      // Submit job
      const result = await submitMDVerificationJob(values)

      message.success(
        <span>
          任务提交成功！
          <br />
          任务ID: <code>{result.id}</code>
        </span>,
      )

      // Reset form
      form.resetFields()

      // Notify parent component
      if (onSuccess) {
        onSuccess(result.id)
      }
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : "提交失败"
      message.error(`任务提交失败: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card
      title="提交 MD 验证任务"
      style={{ maxWidth: 800, margin: "0 auto" }}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{
          element_system: "U",
          temperature: 300,
          pressure: 0,
          simulation_time: 100,
          timestep: 0.001,
          ensemble: "NPT",
          priority: 5,
        }}
      >
        <Form.Item
          label="势函数 ID"
          name="potential_id"
          rules={[{ required: true, message: "请输入势函数ID" }]}
        >
          <Input placeholder="例如: EAM_alloy_U" />
        </Form.Item>

        <Form.Item
          label="元素体系"
          name="element_system"
          rules={[{ required: true, message: "请输入元素体系" }]}
        >
          <Select placeholder="选择元素体系">
            <Option value="U">铀 (U)</Option>
            <Option value="Pu">钚 (Pu)</Option>
            <Option value="Th">钍 (Th)</Option>
            <Option value="U-Pu">U-Pu 合金</Option>
            <Option value="other">其他</Option>
          </Select>
        </Form.Item>

        <Form.Item label="相结构 (可选)" name="phase">
          <Select placeholder="选择相结构" allowClear>
            <Option value="BCC">BCC (体心立方)</Option>
            <Option value="FCC">FCC (面心立方)</Option>
            <Option value="HCP">HCP (密排六方)</Option>
            <Option value="other">其他</Option>
          </Select>
        </Form.Item>

        <Form.Item
          label="势函数文件路径"
          name="potential_file"
          rules={[{ required: true, message: "请输入势函数文件路径" }]}
          extra="服务器上的绝对路径，例如: /data/potentials/U_U.empirical"
        >
          <Input placeholder="/data/potentials/U_U.empirical" />
        </Form.Item>

        <Form.Item
          label="结构文件路径"
          name="structure_file"
          rules={[{ required: true, message: "请输入结构文件路径" }]}
          extra="服务器上的绝对路径，例如: /data/structures/BCC_U.cif"
        >
          <Input placeholder="/data/structures/BCC_U.cif" />
        </Form.Item>

        <Card
          title="模拟参数"
          size="small"
          style={{ marginBottom: 16 }}
          type="inner"
        >
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Form.Item
              label="温度 (K)"
              name="temperature"
              rules={[{ required: true, message: "请输入温度" }]}
            >
              <InputNumber
                min={0}
                max={10000}
                style={{ width: "100%" }}
                addonAfter="K"
              />
            </Form.Item>

            <Form.Item
              label="压力 (GPa)"
              name="pressure"
              rules={[{ required: true, message: "请输入压力" }]}
            >
              <InputNumber
                min={-100}
                max={100}
                step={0.1}
                style={{ width: "100%" }}
                addonAfter="GPa"
              />
            </Form.Item>

            <Form.Item label="模拟时间 (ps，可选)" name="simulation_time">
              <InputNumber
                min={1}
                max={10000}
                style={{ width: "100%" }}
                addonAfter="ps"
              />
            </Form.Item>

            <Form.Item label="时间步长 (ps，可选)" name="timestep">
              <InputNumber
                min={0.0001}
                max={0.01}
                step={0.0001}
                style={{ width: "100%" }}
                addonAfter="ps"
              />
            </Form.Item>

            <Form.Item label="系综类型 (可选)" name="ensemble">
              <Select placeholder="选择系综类型">
                <Option value="NPT">NPT (等温等压)</Option>
                <Option value="NVT">NVT (等温等容)</Option>
                <Option value="NVE">NVE (微正则)</Option>
              </Select>
            </Form.Item>
          </Space>
        </Card>

        <Form.Item label="优先级 (可选)" name="priority">
          <InputNumber
            min={1}
            max={10}
            style={{ width: 120 }}
            placeholder="1-10，数字越大优先级越高"
          />
        </Form.Item>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={loading}
            icon={<SendOutlined />}
            size="large"
            block
          >
            提交任务
          </Button>
        </Form.Item>
      </Form>
    </Card>
  )
}
