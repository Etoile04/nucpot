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

// NFM-5418: without this, a fully-static page emits Next.js's default
// s-maxage=31536000 HTML header, so the Cloudflare edge pins it for a year
// and any deploy that changes chunk hashes orphans the cached refs
// (NFM-5335 class). Match the home page's ISR window instead: the shell
// revalidates every 5 min behind stale-while-revalidate.
export const revalidate = 300

export default function BrowsePage() {
  return <BrowseView />
}
