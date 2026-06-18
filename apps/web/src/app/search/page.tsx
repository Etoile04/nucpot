import type { Metadata } from "next"
import { SearchView } from "./SearchView"

export const metadata: Metadata = {
  title: "高级检索 - NFMD",
  description: "按类型、元素或关键字检索核材料势函数",
}

export default function SearchPage() {
  return <SearchView />
}
