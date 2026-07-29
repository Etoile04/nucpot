import type { Metadata } from "next"

import HubAdminContent from "@/components/admin/hub/HubAdminContent"

export const metadata: Metadata = {
  title: "中心节点管理 - NFMD",
  description: "1+N 架构资源节点注册、心跳与发现管理",
}

export default function HubAdminPage() {
  return <HubAdminContent />
}
