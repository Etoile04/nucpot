import type { Metadata } from "next"
import UploadForm from "./UploadForm"

export const metadata: Metadata = {
  title: "上传势函数 - NFMD",
  description: "上传核材料势函数（Phase 2）",
}

export default function UploadPage() {
  return <UploadForm />
}
