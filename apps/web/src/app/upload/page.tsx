import type { Metadata } from "next"
import { Result, Button } from "antd"
import Link from "next/link"

export const metadata: Metadata = {
  title: "上传势函数 - NFMD",
  description: "上传核材料势函数（Phase 2 即将上线）",
}

export default function UploadStubPage() {
  return (
    <Result
      status="info"
      title="上传功能即将上线"
      subTitle={
        "势函数上传与自动验证功能正在开发中（Phase 2）。\n" +
        "届时将支持势函数文件上传、参数校验、与参考值的自动比对。"
      }
      extra={
        <Link href="/browse">
          <Button type="primary">浏览势函数库</Button>
        </Link>
      }
      style={{ padding: "4rem 1.5rem" }}
    />
  )
}
