"use client"

import { ConfigProvider } from "antd"
import zhCN from "antd/locale/zh_CN"

export function AntdProvider({ children }: { children: React.ReactNode }) {
  return <ConfigProvider locale={zhCN}>{children}</ConfigProvider>
}
