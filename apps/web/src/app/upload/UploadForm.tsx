"use client"

import { useState } from "react"
import type { UploadFile } from "antd"
import {
  Button,
  Form,
  Input,
  message,
  Select,
  Upload,
  Space,
  Card,
  Alert,
} from "antd"
import {
  UploadOutlined,
  FileTextOutlined,
} from "@ant-design/icons"
import { useAuth } from "@/components/AuthProvider"
import { submitPotential, uploadPotentialFile } from "@/lib/upload-api"
import type { CreatedPotential, FileInfo } from "@/lib/upload-api"

const { TextArea } = Input

/**
 * Render trusted HTML into a freshly-opened preview window via a sandboxed
 * iframe `srcdoc`.
 *
 * Security review (NFM-1344, CTO Decision 4): the previous implementation wrote
 * `data.html` straight into a popup document, executing arbitrary HTML/JS in the
 * parent window's own origin — a full XSS surface. We now inject the markup
 * through an iframe whose `sandbox` is limited to `allow-same-origin`
 * (deliberately WITHOUT `allow-scripts`), so the letter renders visually intact
 * while any embedded script is neutralised. `html` is assumed to originate only
 * from the trusted internal `/api/auth/template` endpoint.
 */
export function renderTrustedHtmlPreview(win: Window, html: string): void {
  const doc = win.document
  doc.body.style.margin = "0"
  const iframe = doc.createElement("iframe")
  iframe.setAttribute("sandbox", "allow-same-origin")
  iframe.srcdoc = html
  iframe.style.border = "none"
  iframe.style.width = "100%"
  iframe.style.height = "100vh"
  doc.body.appendChild(iframe)
}

const POTENTIAL_TYPES = [
  { label: "EAM", value: "EAM" },
  { label: "MEAM", value: "MEAM" },
  { label: "MTP", value: "MTP" },
  { label: "ACE", value: "ACE" },
  { label: "LJ", value: "LJ" },
  { label: "SNAP", value: "SNAP" },
  { label: "ReaxFF", value: "ReaxFF" },
  { label: "Tersoff", value: "Tersoff" },
  { label: "SW", value: "SW" },
  { label: "BOP", value: "BOP" },
  { label: "COMB", value: "COMB" },
  { label: "DP", value: "DP" },
  { label: "其他", value: "other" },
]

const LICENSE_TYPES = [
  { label: "自有作品", value: "own_work" },
  { label: "已获作者授权", value: "author_permission" },
  { label: "开放许可", value: "open_license" },
]

export default function UploadForm() {
  const [form] = Form.useForm()
  const { user } = useAuth()
  const [loading, setLoading] = useState(false)
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [licenseType, setLicenseType] = useState<string | undefined>()
  const [result, setResult] = useState<{ potential: CreatedPotential; fileInfo?: FileInfo } | null>(null)
  const [templateLoading, setTemplateLoading] = useState(false)

  // Generate authorization template with form data pre-filled
  const generateTemplate = async (lang: 'zh' | 'en') => {
    setTemplateLoading(true)
    try {
      const values = form.getFieldsValue()
      const params = new URLSearchParams({
        lang,
        name: (values.name as string) || '',
        type: (values.type as string) || '',
        elements: Array.isArray(values.elements) ? values.elements.join(', ') : (values.elements as string) || '',
        systemName: (values.system_name as string) || '',
        doiRefs: '',
        userName: user?.full_name || user?.email || '',
        userEmail: user?.email || '',
        print: '1',
      })
      const res = await fetch(`/api/auth/template?${params}`)
      const data = await res.json()
      if (data.html) {
        const w = window.open('', '_blank')
        if (w) {
          renderTrustedHtmlPreview(w, data.html as string)
        }
      }
    } catch {
      message.error('模板生成失败')
    } finally {
      setTemplateLoading(false)
    }
  }

  const handleFinish = async (values: Record<string, unknown>) => {
    setLoading(true)
    setResult(null)
    try {
      const metadata = {
        name: values.name as string,
        display_name: (values.display_name as string | undefined) || undefined,
        type: values.type as string,
        subtype: (values.subtype as string | undefined) || undefined,
        format: (values.format as string | undefined) || undefined,
        elements: cleanElements(values.elements as string[] | string | undefined),
        system_name: values.system_name as string,
        description: values.description as string,
        license_type: values.license_type as "own_work" | "author_permission" | "open_license",
        license_detail: (values.license_detail as string | undefined) || undefined,
        auth_file_path: (values.auth_file_path as string | undefined) || undefined,
      }

      const submitResult = await submitPotential(metadata)
      if (!submitResult.success) {
        message.error(submitResult.error)
        // Surface duplicate-name error on the name field
        if (submitResult.status === 409) {
          form.setFields([{ name: "name", errors: [submitResult.error] }])
        }
        setLoading(false)
        return
      }

      const created = submitResult.potential
      let fileInfo: FileInfo | undefined
      if (fileList.length > 0) {
        const fileObj = fileList[0]?.originFileObj
        if (!fileObj) {
          message.error("文件无效")
          setLoading(false)
          return
        }
        const uploadResult = await uploadPotentialFile(created.id, fileObj as File)
        if (!uploadResult.success) {
          message.error(`文件上传失败: ${uploadResult.error}`)
          setLoading(false)
          return
        }
        fileInfo = uploadResult.file_info
      }

      setResult({ potential: created, fileInfo })
      message.success("势函数上传成功")
      form.resetFields()
      setFileList([])
      setLicenseType(undefined)
    } catch (err) {
      message.error(err instanceof Error ? err.message : "上传失败")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card title="上传势函数" className="max-w-[720px] mx-auto my-8">
      <Form
        form={form}
        layout="vertical"
        onFinish={handleFinish}
        initialValues={{ license_type: "own_work" }}
      >
        <Form.Item
          name="name"
          label="名称"
          rules={[
            { required: true, message: "名称为必填项" },
            { max: 256, message: "名称不能超过256个字符" },
          ]}
        >
          <Input placeholder="例如: EAM_U_Zhou_2004" />
        </Form.Item>

        <Form.Item name="display_name" label="显示名称">
          <Input placeholder="可选，默认使用名称" />
        </Form.Item>

        <Form.Item
          name="type"
          label="类型"
          rules={[{ required: true, message: "类型为必填项" }]}
        >
          <Select options={POTENTIAL_TYPES} placeholder="选择势函数类型" />
        </Form.Item>

        <Form.Item name="subtype" label="子类型">
          <Input placeholder="可选" />
        </Form.Item>

        <Form.Item name="format" label="格式">
          <Input placeholder="默认 LAMMPS" />
        </Form.Item>

        <Form.Item
          name="elements"
          label="元素"
          rules={[{ required: true, type: "array", min: 1, message: "至少需要一个元素" }]}
          getValueFromEvent={(val: string[] | undefined) => val}
        >
          <Select
            mode="tags"
            placeholder="输入元素符号后按回车（如 U, Mo）"
            style={{ width: "100%" }}
          />
        </Form.Item>

        <Form.Item
          name="system_name"
          label="体系名称"
          rules={[{ required: true, message: "体系名称为必填项" }]}
        >
          <Input placeholder="例如: U-Mo alloy" />
        </Form.Item>

        <Form.Item
          name="description"
          label="描述"
          rules={[{ required: true, message: "描述为必填项" }]}
        >
          <TextArea rows={3} placeholder="简要描述该势函数" />
        </Form.Item>

        <Form.Item
          name="license_type"
          label="许可类型"
          rules={[{ required: true, message: "许可类型为必填项" }]}
        >
          <Select
            options={LICENSE_TYPES}
            onChange={(val: string) => setLicenseType(val)}
          />
        </Form.Item>

        {licenseType === "open_license" && (
          <Form.Item
            name="license_detail"
            label="许可名称"
            rules={[{ required: true, message: "开放许可需提供许可名称" }]}
          >
            <Input placeholder="例如: CC-BY-4.0" />
          </Form.Item>
        )}

        {licenseType === "author_permission" && (
          <>
            <Alert
              type="info"
              showIcon
              message="需要作者授权书"
              description={
                <div className="text-sm">
                  <p className="mb-2">
                    请下载授权书模板，填写势函数信息后<strong>打印、签字、扫描</strong>，
                    再上传扫描件。模板会自动填入当前表单中的信息。
                  </p>
                  <Space>
                    <Button
                      size="small"
                      type="primary"
                      ghost
                      icon={<FileTextOutlined />}
                      loading={templateLoading}
                      onClick={() => generateTemplate('zh')}
                    >
                      生成中文授权书
                    </Button>
                    <Button
                      size="small"
                      icon={<FileTextOutlined />}
                      loading={templateLoading}
                      onClick={() => generateTemplate('en')}
                    >
                      English Authorization
                    </Button>
                  </Space>
                </div>
              }
              className="!mb-4"
            />
            <Form.Item
              name="auth_file_path"
              label="授权证明文件路径或链接"
              rules={[{ required: true, message: "作者授权需提供授权证明" }]}
            >
              <Input placeholder="上传扫描件后填入路径，或填写外部链接" />
            </Form.Item>
          </>
        )}

        <Form.Item label="文件上传（可选）">
          <Upload
            beforeUpload={() => false}
            fileList={fileList}
            onChange={({ fileList: fl }) => {
              setFileList(fl.slice(-1))
            }}
            maxCount={1}
            accept=".eam,.eam.alloy,.eam.fs,.setfl,.meam,.param,.table,.mtp,.snap,.json,.txt,.zip,.tar.gz,.gz,.reaxff,.tersoff,.sw,.bop,.comb,.lj,.dp"
          >
            <Button icon={<UploadOutlined />}>选择文件</Button>
          </Upload>
        </Form.Item>

        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={loading} size="large">
              上传
            </Button>
            <Button onClick={() => form.resetFields()} size="large">
              重置
            </Button>
          </Space>
        </Form.Item>
      </Form>

      {result && (
        <Card
          size="small"
          title="上传成功"
          className="!bg-green-900/30 mt-4"
        >
          <p>ID: {result.potential.id}</p>
          <p>名称: {result.potential.name}</p>
          <p>状态: {result.potential.status}</p>
          {result.fileInfo && <p>文件: {result.fileInfo.file_name}</p>}
        </Card>
      )}
    </Card>
  )
}

function cleanElements(elements: string[] | string | undefined): string[] {
  if (!elements) return []
  if (Array.isArray(elements)) return elements.filter(Boolean)
  return elements
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}
