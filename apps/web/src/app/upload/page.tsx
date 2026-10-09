import type { Metadata } from "next"
import UploadForm from "./UploadForm"

export const metadata: Metadata = {
  title: "上传势函数 - NFMD",
  description: "上传核材料势函数（Phase 2）",
}

// NFM-5418: without this, a fully-static page emits Next.js's default
// s-maxage=31536000 HTML header, so the Cloudflare edge pins it for a year
// and any deploy that changes chunk hashes orphans the cached refs
// (NFM-5335 class). Match the home page's ISR window instead: the shell
// revalidates every 5 min behind stale-while-revalidate.
export const revalidate = 300

export default function UploadPage() {
  return <UploadForm />
}
