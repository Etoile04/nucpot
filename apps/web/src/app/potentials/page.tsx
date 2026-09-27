import type { Metadata } from "next"
import { BrowseView } from "./BrowseView"

// NFM-5228: /browse now alias-serves this page (afterFiles rewrite) instead
// of 308-redirecting to it, so both paths return 200. The canonical link
// keeps search signals consolidated on /potentials. metadataBase anchors the
// relative canonical to the deployed origin; without it Next.js resolves
// metadata URLs against a localhost default behind the proxy.
const siteUrl = process.env.NEXT_PUBLIC_APP_URL ?? "https://nucpot.dpdns.org"

export const metadata: Metadata = {
  title: "浏览势函数 - NFMD",
  description: "浏览核材料势函数库",
  metadataBase: new URL(siteUrl),
  alternates: {
    canonical: "/potentials",
  },
}

export default function BrowsePage() {
  return <BrowseView />
}
