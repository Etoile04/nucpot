import { Suspense } from "react"
import { Spin } from "antd"
import { SearchPageContent } from "./SearchPageContent"

export const revalidate = 300

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center items-center min-h-[400px]">
          <Spin tip="加载中...">
            <div />
          </Spin>
        </div>
      }
    >
      <SearchPageContent />
    </Suspense>
  )
}
