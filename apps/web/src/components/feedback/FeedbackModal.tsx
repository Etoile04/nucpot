"use client"

import { useCallback, useState } from "react"
import {
  Button,
  Form,
  Input,
  Modal,
  Radio,
  App,
} from "antd"
import {
  BugOutlined,
  BulbOutlined,
  EditOutlined,
  QuestionCircleOutlined,
} from "@ant-design/icons"
import {
  FEEDBACK_TYPES,
  submitFeedback,
  type FeedbackPayload,
} from "@/lib/feedback-api"

const TYPE_ICONS: Record<string, React.ReactNode> = {
  bug_report: <BugOutlined />,
  feature_request: <BulbOutlined />,
  data_correction: <EditOutlined />,
  usage_inquiry: <QuestionCircleOutlined />,
}

interface FeedbackModalProps {
  open: boolean
  onClose: () => void
}

interface FeedbackFormValues {
  feedback_type: string
  title: string
  description: string
  contact_email?: string
}

export function FeedbackModal({ open, onClose }: FeedbackModalProps) {
  const [form] = Form.useForm<FeedbackFormValues>()
  const [submitting, setSubmitting] = useState(false)
  const { message } = App.useApp()

  const handleReset = useCallback(() => {
    form.resetFields()
  }, [form])

  const handleClose = useCallback(() => {
    handleReset()
    onClose()
  }, [handleReset, onClose])

  const handleSubmit = useCallback(
    async (values: FeedbackFormValues) => {
      setSubmitting(true)
      try {
        const payload: FeedbackPayload = {
          ...values,
          page_url: window.location.href,
        }

        await submitFeedback(payload)
        message.success("感谢您的反馈！我们会尽快处理。")
        handleClose()
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "提交失败，请稍后重试"
        message.error(errorMessage)
      } finally {
        setSubmitting(false)
      }
    },
    [handleClose, message],
  )

  return (
    <Modal
      title="意见反馈"
      open={open}
      onCancel={handleClose}
      footer={null}
      destroyOnClose
      width={520}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{ feedback_type: "bug_report" }}
        requiredMark
      >
        <Form.Item
          name="feedback_type"
          label="问题类型"
          rules={[{ required: true, message: "请选择问题类型" }]}
        >
          <Radio.Group>
            {FEEDBACK_TYPES.map((type) => (
              <Radio key={type.value} value={type.value}>
                {TYPE_ICONS[type.value]}
                {" "}
                {type.label}
              </Radio>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item
          name="title"
          label="简要描述"
          rules={[
            { required: true, message: "请输入简要描述" },
            { max: 100, message: "简要描述不能超过 100 个字符" },
          ]}
        >
          <Input
            placeholder="一句话概括您的问题或建议"
            maxLength={100}
            showCount
          />
        </Form.Item>

        <Form.Item
          name="description"
          label="详细描述"
          rules={[
            { required: true, message: "请输入详细描述" },
            { max: 2000, message: "详细描述不能超过 2000 个字符" },
          ]}
        >
          <Input.TextArea
            placeholder="请详细描述您遇到的问题或建议..."
            rows={5}
            maxLength={2000}
            showCount
          />
        </Form.Item>

        <Form.Item
          name="contact_email"
          label="联系邮箱（可选）"
          rules={[
            { type: "email", message: "请输入有效的邮箱地址" },
          ]}
        >
          <Input placeholder="方便我们与您联系" />
        </Form.Item>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={submitting}
            block
          >
            提交反馈
          </Button>
        </Form.Item>
      </Form>
    </Modal>
  )
}
