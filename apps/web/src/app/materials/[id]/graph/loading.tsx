import { Spin } from "antd"

export default function Loading() {
  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <div className="flex items-center justify-center py-32">
        <Spin size="large" tip="Loading graph…" />
      </div>
    </main>
  )
}