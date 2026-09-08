"use client"

import Link from "next/link"
import { Button } from "antd"
import { DownloadOutlined } from "@ant-design/icons"
import { resolveFileName, resolveFileUrl } from "@/lib/file-url"

export type FileLinkVariant = "button" | "link"

/**
 * Minimal shape FileLink needs from a potential — works with both
 * `PotentialSummary` (list rows, no `extra`) and `PotentialDetail`
 * (full record with `extra.file_storage`).
 */
export interface FileLinkPotential {
  file_url?: string | null
  extra?: Record<string, unknown> | null
}

interface FileLinkProps {
  readonly potential: FileLinkPotential
  /**
   * "button" (default): wraps the download in an AntD primary button
   * with the 下载 label — used on detail pages and the submission
   * wizard summary where a clear download CTA matters.
   *
   * "link": renders a bare anchor — used inline in dense rows.
   */
  readonly variant?: FileLinkVariant
}

/**
 * Single canonical download surface for a potential's `file_url`
 * (NFM-4458).
 *
 * Trusts the backend-canonicalized proxy URL (NFM-4309 / migration 083).
 * When `file_url` is empty (≈18 historical BUG-06 rows with no
 * underlying file), renders a "文件缺失" text node instead of a link
 * so the user gets an honest "not available" signal instead of a
 * silently-broken anchor.
 *
 * The 4-format interpreter and the Supabase origin completion that
 * used to live in `lib/file-url.ts` were removed: production has zero
 * non-canonical rows left, and silent rewriting would mask any future
 * data-integrity regression instead of surfacing it.
 */
export function FileLink({ potential, variant = "button" }: FileLinkProps) {
  const href = resolveFileUrl(potential.file_url)

  if (!href) {
    return (
      <span className="text-muted" data-testid="filelink-missing">
        文件缺失
      </span>
    )
  }

  const fileName = resolveFileName(href, potential.extra)

  if (variant === "link") {
    return (
      <Link href={href} download={fileName}>
        {fileName}
      </Link>
    )
  }

  return (
    <Link href={href} download={fileName}>
      <Button type="primary" icon={<DownloadOutlined />}>
        下载
      </Button>
    </Link>
  )
}
