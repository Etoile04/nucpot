/** Register-new-node modal form (NFM-2023, AC-4).
 *
 * Validates input client-side (mirroring the NodeRegisterRequest
 * constraints from NFM-2022) and calls the hub register endpoint.
 */

"use client"

import { Form, Input, Modal, Select } from "antd"

import { registerHubNode } from "@/lib/admin/hub-api"
import type { NodeRegisterRequest, ResourceNode } from "@/lib/admin/hub-types"
import { useFormSubmit, FormAlert } from "@/components/forms"

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const NODE_TYPE_OPTIONS = [
  { value: "computing", label: "计算节点 (computing)" },
  { value: "storage", label: "存储节点 (storage)" },
  { value: "observatory", label: "观测节点 (observatory)" },
]

interface RegisterNodeModalProps {
  open: boolean
  onClose: () => void
  onRegistered: (node: ResourceNode) => void
}

export default function RegisterNodeModal({ open, onClose, onRegistered }: RegisterNodeModalProps) {
  const [form] = Form.useForm<NodeRegisterRequest>()

  const submit = useFormSubmit<NodeRegisterRequest, ResourceNode>({
    mutationFn: (values) =>
      registerHubNode({
        ...values,
        public_key: values.public_key || null,
      }),
    errorFallback: "注册节点失败",
    onSuccess: (node) => {
      form.resetFields()
      onRegistered(node)
    },
  })

  const handleOk = async () => {
    let values: NodeRegisterRequest
    try {
      values = await form.validateFields()
    } catch {
      return // Validation errors are rendered inline by the form.
    }
    try {
      await submit.submit(values)
    } catch {
      // surfaced via submit.error / FormAlert
    }
  }

  const handleCancel = () => {
    form.resetFields()
    submit.reset()
    onClose()
  }

  return (
    <Modal
      title="注册新节点"
      open={open}
      onOk={handleOk}
      onCancel={handleCancel}
      okText="注册"
      cancelText="取消"
      confirmLoading={submit.isSubmitting}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" name="register-node">
        <Form.Item
          name="hub_node_id"
          label="所属中心节点 ID"
          rules={[
            { required: true, message: "请输入中心节点 ID" },
            { pattern: UUID_PATTERN, message: "必须是合法的 UUID" },
          ]}
        >
          <Input placeholder="例如 3fa85f64-5717-4562-b3fc-2c963f66afa6" />
        </Form.Item>
        <Form.Item
          name="name"
          label="节点名称"
          rules={[
            { required: true, message: "请输入节点名称" },
            { max: 200, message: "名称不能超过 200 字符" },
          ]}
        >
          <Input placeholder="例如 西南所-计算节点" />
        </Form.Item>
        <Form.Item
          name="node_type"
          label="节点类型"
          rules={[{ required: true, message: "请选择节点类型" }]}
        >
          <Select options={NODE_TYPE_OPTIONS} placeholder="选择节点类型" />
        </Form.Item>
        <Form.Item
          name="api_endpoint"
          label="API 地址"
          rules={[
            { required: true, message: "请输入节点 API 地址" },
            { type: "url", message: "必须是合法的 URL" },
            { max: 500, message: "地址不能超过 500 字符" },
          ]}
        >
          <Input placeholder="https://node.example.org" />
        </Form.Item>
        <Form.Item
          name="public_key"
          label="公钥 (可选)"
          rules={[{ max: 2000, message: "公钥不能超过 2000 字符" }]}
        >
          <Input.TextArea rows={3} placeholder="用于加密校验的公钥" />
        </Form.Item>
      </Form>
      {submit.status === "error" ? (
        <FormAlert
          status={submit.status}
          error={submit.error}
          theme="antd"
          onRetry={handleOk}
          retryLabel="重试"
        />
      ) : null}
    </Modal>
  )
}
