import type { Metadata } from "next"
import AdminKgContent from "./AdminKgContent"

export const metadata: Metadata = {
  title: "知识图谱管理 - NFMD",
  description: "知识图谱节点统计与审核管理",
}

export default function AdminKgPage() {
  return <AdminKgContent />
}
