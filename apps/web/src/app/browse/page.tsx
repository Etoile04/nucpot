import type { Metadata } from "next"
import { BrowseView } from "./BrowseView"

export const metadata: Metadata = {
  title: "浏览势函数 - NFMD",
  description: "浏览核材料势函数库",
}

export default function BrowsePage() {
  return <BrowseView />
}
