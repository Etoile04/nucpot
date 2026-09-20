import type { Metadata } from "next"
import Link from "next/link"
import { Alert, Descriptions, Spin, Tag } from "antd"
import {
  getDatasetServer,
  type DatasetDetail,
} from "@/lib/datasets-server"

interface PageProps {
  params: Promise<{ id: string }>
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { id } = await params
  return {
    title: `数据集 ${id.slice(0, 8)} - NucPot`,
    description: "核材料数据集详情，包含 §5.2 attribution 块 (NFM-4159)。",
  }
}

async function fetchDataset(id: string): Promise<DatasetDetail> {
  // Server component → Node fetch: the server module resolves an
  // absolute API base (NFM-5020) and unwraps the envelope; throw on
  // error so the page can render a friendly error boundary.
  return await getDatasetServer(id)
}

export default async function DatasetDetailPage({ params }: PageProps) {
  const { id } = await params
  let dataset: DatasetDetail | null = null
  let errorMessage: string | null = null

  try {
    dataset = await fetchDataset(id)
  } catch (err) {
    errorMessage =
      err instanceof Error ? err.message : "数据集加载失败"
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="mx-auto max-w-4xl px-6 py-10 space-y-6">
        <nav className="text-sm text-gray-400">
          <Link href="/datasets" className="hover:text-blue-300 hover:underline">
            ← 返回数据集列表
          </Link>
        </nav>

        {errorMessage ? (
          <Alert
            type="error"
            showIcon
            message="数据集加载失败"
            description={errorMessage}
          />
        ) : null}

        {!errorMessage && !dataset ? (
          <div className="flex justify-center py-12">
            <Spin />
          </div>
        ) : null}

        {dataset ? (
          <>
            <header className="space-y-2">
              <h1 className="text-3xl font-bold">{dataset.title}</h1>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-mono text-xs text-gray-400">
                  {dataset.id}
                </span>
                {dataset.is_verified ? (
                  <Tag color="green">已审核</Tag>
                ) : (
                  <Tag color="default">未审核</Tag>
                )}
                <AttributionBadge status={dataset.attribution.status} />
              </div>
            </header>

            {/* NFM-4159 §5.2 attribution disclosure: when status is
                'placeholder' the dataset title already carries the
                disclosure per CEO §4.2 — this block is a confirmation
                banner, not the primary disclosure surface. */}
            {dataset.attribution.status === "placeholder" ? (
              <Alert
                type="warning"
                showIcon
                message="占位数据集 (placeholder)"
                description={
                  <span>
                    此数据集来自迁移 070 的 recast 复原集合(参见 NFM-4159
                    §5.2 与 NFM-4136)。placeholder 状态是合规披露而非异常,
                    标题字段本身就是披露渠道。
                  </span>
                }
              />
            ) : null}

            <Descriptions
              column={1}
              bordered
              size="middle"
              className="text-gray-200"
              items={[
                {
                  key: "material",
                  label: "材料",
                  children: (
                    <Link
                      href={`/materials/${dataset.material_id}`}
                      className="font-mono text-xs text-blue-400 hover:text-blue-300 hover:underline"
                    >
                      {dataset.material_id}
                    </Link>
                  ),
                },
                {
                  key: "source",
                  label: "数据源",
                  children:
                    dataset.source_id ? (
                      <Link
                        href={`/publications/${dataset.source_id}`}
                        className="font-mono text-xs text-blue-400 hover:text-blue-300 hover:underline"
                      >
                        {dataset.source_id}
                      </Link>
                    ) : (
                      <span className="text-gray-500">无</span>
                    ),
                },
                {
                  key: "measurement_date",
                  label: "测量日期",
                  children: dataset.measurement_date ?? "—",
                },
                {
                  key: "description",
                  label: "描述",
                  children: dataset.description ? (
                    <span className="whitespace-pre-wrap text-sm text-gray-300">
                      {dataset.description}
                    </span>
                  ) : (
                    <span className="text-gray-500">无</span>
                  ),
                },
                {
                  key: "created",
                  label: "创建时间",
                  children: new Date(dataset.created_at).toISOString().slice(0, 19),
                },
                {
                  key: "updated",
                  label: "更新时间",
                  children: new Date(dataset.updated_at).toISOString().slice(0, 19),
                },
              ]}
            />
          </>
        ) : null}
      </main>
    </div>
  )
}

function AttributionBadge({
  status,
}: {
  status: "placeholder" | "intact"
}) {
  if (status === "placeholder") {
    return <Tag color="orange">placeholder</Tag>
  }
  return <Tag color="blue">intact</Tag>
}