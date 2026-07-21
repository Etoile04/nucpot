/**
 * Antd SSR provider.
 *
 * NFM-1704: Wrap `ConfigProvider` with `@ant-design/nextjs-registry`'s
 * `AntdRegistry` so the cssinjs cache is rendered into the SSR HTML via
 * `useServerInsertedHTML`. Without this registry, Antd injects styles into
 * the document <head> at client mount time, causing a large cumulative
 * layout shift (~0.3 on /design). With it, all cssinjs output ships in
 * the initial HTML, so the first paint already has the right
 * typography/spacing — eliminating the late-injection shift.
 *
 * Why this is a server-side Registry wrapper but the component itself is a
 * client component: `AntdRegistry` internally calls `useServerInsertedHTML`
 * which is only valid inside a client component (Next.js runtime hint).
 */

"use client"

import { App, ConfigProvider, theme } from "antd"
import { AntdRegistry } from "@ant-design/nextjs-registry"
import zhCN from "antd/locale/zh_CN"

export function AntdProvider({ children }: { children: React.ReactNode }) {
  return (
    <AntdRegistry>
      <ConfigProvider
        locale={zhCN}
        theme={{ algorithm: theme.darkAlgorithm }}
      >
        <App>{children}</App>
      </ConfigProvider>
    </AntdRegistry>
  )
}
