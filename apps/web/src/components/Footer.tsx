"use client"

import { usePathname } from "next/navigation"

/** Paths where the footer is hidden to maximize embedded viewer space. */
const HIDE_PATHS = ["/ontology"] as const

export default function Footer() {
  const pathname = usePathname()

  const isHidden = HIDE_PATHS.some((p) => pathname.startsWith(p))

  if (isHidden) {
    return null
  }

  return (
    // NFM-3794 a11y: `!text-gray-400` overrides antd's `:where(.css-plsjn) a`
    // rule which would otherwise force every <a> to the 3.42:1 #1668dc
    // colorLink. `!text-blue-400 hover:!text-blue-300` on the mailto link
    // ensures we hit ≥4.5:1 (blue-400 #60a5fa = 8.59:1 on #101828).
    <footer className="border-t border-gray-700 py-8 text-center !text-gray-400 text-sm">
      <p>
        反馈与建议：
        <a
          href="mailto:feedback@nucpot.org"
          className="!text-blue-400 hover:!text-blue-300"
        >
          feedback@nucpot.org
        </a>
      </p>
      <p>&copy; {new Date().getFullYear()} 核燃料与材料物性数据库</p>
    </footer>
  )
}
