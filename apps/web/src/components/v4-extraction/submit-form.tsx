"use client"

import { Form, Input, Select, Button, Radio, message } from "antd"
import { SendOutlined } from "@ant-design/icons"
import { useRouter } from "next/navigation"
import { useMutation } from "@tanstack/react-query"
import { submitExtractionJob } from "@/lib/v4-extraction/api"
import { ELEMENT_SYSTEM_PRESETS } from "@/lib/v4-extraction/constants"
import type {
  CacheLevel,
  Confidence,
  Priority,
  SourceType,
  V4ExtractionSubmitRequest,
} from "@/lib/v4-extraction/types"

const { Option } = Select

const SOURCE_TYPE_OPTIONS: { value: SourceType; label: string }[] = [
  { value: "doi", label: "DOI" },
  { value: "url", label: "URL" },
  { value: "file", label: "文件路径" },
  { value: "internal_id", label: "内部ID" },
]

const CACHE_LEVEL_OPTIONS: { value: CacheLevel; label: string }[] = [
  { value: "L1", label: "L1 直测" },
  { value: "L2", label: "L2 文献" },
  { value: "L3A", label: "L3A 插值" },
  { value: "L3B", label: "L3B 模拟" },
]

const CONFIDENCE_OPTIONS: { value: Confidence; label: string }[] = [
  { value: "high", label: "高 / High" },
  { value: "medium", label: "中 / Medium" },
  { value: "low", label: "低 / Low" },
]

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "normal", label: "普通 / Normal" },
  { value: "high", label: "高 / High" },
]

export function SubmitForm() {
  const [form] = Form.useForm()
  const router = useRouter()
  const [messageApi, contextHolder] = message.useMessage()

  const mutation = useMutation({
    mutationFn: (payload: V4ExtractionSubmitRequest) =>
      submitExtractionJob(payload),
    onSuccess: (data) => {
      messageApi.success("任务提交成功！")
      form.resetFields()
      router.push(`/admin/v4-extraction/status/${data.job_id}`)
    },
    onError: (error: unknown) => {
      const errorMessage =
        error instanceof Error ? error.message : "提交失败"
      messageApi.error(`任务提交失败: ${errorMessage}`)
    },
  })

  const handleFinish = (values: V4ExtractionSubmitRequest) => {
    mutation.mutate(values)
  }

  return (
    <>
      {contextHolder}
      <Form<V4ExtractionSubmitRequest>
        form={form}
        layout="vertical"
        onFinish={handleFinish}
        initialValues={{
          source_type: "doi",
          cache_level: "L2",
          priority: "normal",
        }}
      >
        <Form.Item<V4ExtractionSubmitRequest>
          label="来源类型 / Source Type"
          name="source_type"
          rules={[{ required: true, message: "请选择来源类型" }]}
        >
          <Radio.Group optionType="button" buttonStyle="solid">
            {SOURCE_TYPE_OPTIONS.map((opt) => (
              <Radio.Button key={opt.value} value={opt.value}>
                {opt.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item<V4ExtractionSubmitRequest>
          label="来源标识 / Source Reference"
          name="source_reference"
          rules={[{ required: true, message: "请输入来源标识" }]}
        >
          <Input
            maxLength={500}
            showCount
            placeholder="DOI、URL、文件路径或内部ID"
          />
        </Form.Item>

        <Form.Item<V4ExtractionSubmitRequest>
          label="元素体系 / Element Systems"
          name="element_systems"
        >
          <Select
            mode="tags"
            placeholder="输入或选择元素体系"
            allowClear
          >
            {ELEMENT_SYSTEM_PRESETS.map((preset) => (
              <Option key={preset.value} value={preset.value}>
                {preset.label}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item<V4ExtractionSubmitRequest>
          label="缓存级别 / Cache Level"
          name="cache_level"
        >
          <Radio.Group>
            {CACHE_LEVEL_OPTIONS.map((opt) => (
              <Radio.Button key={opt.value} value={opt.value}>
                {opt.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item<V4ExtractionSubmitRequest>
          label="最低置信度 / Min Confidence"
          name="max_confidence"
        >
          <Select placeholder="不限" allowClear>
            {CONFIDENCE_OPTIONS.map((opt) => (
              <Option key={opt.value} value={opt.value}>
                {opt.label}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item<V4ExtractionSubmitRequest>
          label="优先级 / Priority"
          name="priority"
        >
          <Radio.Group>
            {PRIORITY_OPTIONS.map((opt) => (
              <Radio.Button key={opt.value} value={opt.value}>
                {opt.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={mutation.isPending}
            icon={<SendOutlined />}
            size="large"
            block
          >
            提交任务 / Submit Job
          </Button>
        </Form.Item>
      </Form>
    </>
  )
}
