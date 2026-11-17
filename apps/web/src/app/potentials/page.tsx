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
// (NFM-5335 class). /browse alias-serves this page (NFM-5228), so this
// window also covers the /browse probe path. NFM-5863 widened the window
// to 30 min: the emitted s-maxage must sit above the site-monitor probe
// cadence (~320-400s observed) so edge probes never land on the cold-fill
// path (5.3-9.4s P1 singles, NFM-5862), while staying below the NFM-5418
// stale-pin lane ceiling of 3600s. Match the home page's ISR window: the
// shell revalidates every 30 min behind stale-while-revalidate.
export const revalidate = 1800

export default function BrowsePage() {
  return <BrowseView />
}
