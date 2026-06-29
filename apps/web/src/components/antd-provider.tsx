"use client"

import { App, ConfigProvider, theme } from "antd"
import zhCN from "antd/locale/zh_CN"

export function AntdProvider({ children }: { children: React.ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{ algorithm: theme.darkAlgorithm }}
    >
      <App>{children}</App>
    </ConfigProvider>
  )
}
