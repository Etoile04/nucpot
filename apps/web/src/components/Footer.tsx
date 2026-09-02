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
        {/* NFM-3803: text-blue-400 (#60a5fa) renders 6.8:1 in isolation, but
            Ant Design v5's CSS-in-JS reset injects `a { color: var(--ant-color-link); }`
            at runtime (resolving to #1668dc on the dark theme), which collapsed
            the on-screen contrast to 3.42:1 in the Lighthouse a11y audit. The
            bang prefix (`!text-`) is Tailwind v4's syntax for `!important`,
            which guarantees the local color wins the cascade. */}
        <a
          href="mailto:nucpot@agentmail.to"
          className="!text-blue-300 hover:!text-blue-200"
        >
          nucpot@agentmail.to
        </a>
      </p>
      <p>&copy; {new Date().getFullYear()} 核燃料与材料物性数据库</p>
    </footer>
  )
}
