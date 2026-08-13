import type { Metadata } from "next"

import HubAdminContent from "@/components/admin/hub/HubAdminContent"

export const metadata: Metadata = {
  title: "资源节点管理 - NFMD",
  description: "1+N 架构资源节点拓扑、同步状态与冲突管理",
}

export default function HubAdminPage() {
  return <HubAdminContent />
}
