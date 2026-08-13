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
    <footer className="border-t border-gray-700 py-8 text-center text-gray-400 text-sm">
      <p>
        反馈与建议：
        <a
          href="mailto:feedback@nucpot.org"
          className="text-blue-400 hover:text-blue-300"
        >
          feedback@nucpot.org
        </a>
      </p>
      <p>&copy; {new Date().getFullYear()} 核燃料与材料物性数据库</p>
    </footer>
  )
}
