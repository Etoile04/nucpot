"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Spin, Empty, Tabs, Typography, Space, Tag } from "antd"
import type { PotentialDetail } from "@/lib/potentials-api"
import { getPotential } from "@/lib/potentials-api"
import { PotentialOverview } from "./PotentialOverview"
import { PotentialVerifiedProps } from "./PotentialVerifiedProps"
import { PotentialLammpsGen } from "./PotentialLammpsGen"
import { PotentialDownloads } from "./PotentialDownloads"

const { Title, Text } = Typography

interface PotentialDetailPageProps {
  readonly id: string
}

interface LoadState {
  readonly detail: PotentialDetail | null
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL: LoadState = { detail: null, loading: true, error: null }

export function PotentialDetailPage({ id }: PotentialDetailPageProps) {
  const [state, setState] = useState<LoadState>(INITIAL)

  useEffect(() => {
    let cancelled = false
    setState({ detail: null, loading: true, error: null })
    getPotential(id)
      .then((detail) => {
        if (!cancelled) {
          setState({ detail, loading: false, error: null })
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            detail: null,
            loading: false,
            error: err instanceof Error ? err.message : "加载失败",
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [id])

  if (state.loading) {
    return (
      <main className="py-16 px-6 text-center">
        <Spin size="large" tip="加载中...">
          <div className="min-h-[200px]" />
        </Spin>
      </main>
    )
  }

  if (state.error || !state.detail) {
    return (
      <main className="py-16 px-6">
        <Empty description={state.error ?? "未找到势函数"} />
      </main>
    )
  }

  const detail = state.detail
  const title = detail.display_name ?? detail.name

  const tabItems = [
    {
      key: "overview",
      label: "概览",
      children: <PotentialOverview detail={detail} />,
    },
    {
      key: "verified",
      label: "验证属性",
      children: <PotentialVerifiedProps detail={detail} />,
    },
    {
      key: "lammps",
      label: "LAMMPS 脚本",
      children: <PotentialLammpsGen detail={detail} />,
    },
    {
      key: "downloads",
      label: "下载",
      children: <PotentialDownloads detail={detail} />,
    },
  ]

  return (
    <main className="max-w-[1000px] mx-auto px-6 py-8">
      <Space direction="vertical" size="small" style={{ width: "100%" }} className="mb-4">
        <Link href="/browse">← 返回列表</Link>
        <Title level={2} style={{ margin: 0 }}>
          {title}
          {detail.version && detail.version !== "1.0" && (
            <Text type="secondary" className="text-xs ml-2">
              v{detail.version}
            </Text>
          )}
        </Title>
        <Space wrap size={[0, 4]}>
          <Tag color="blue">{detail.type}</Tag>
          {detail.elements.length > 0 && <Tag>{detail.elements.join("-")}</Tag>}
          {detail.sim_software.map((s) => (
            <Tag key={s} color="default">
              {s}
            </Tag>
          ))}
        </Space>
      </Space>
      <Tabs items={tabItems} />
    </main>
  )
}
