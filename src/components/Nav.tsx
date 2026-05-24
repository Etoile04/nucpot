'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const NAV_LINKS = [
  { href: '/browse', label: '浏览' },
  { href: '/search', label: '高级检索' },
  { href: '/about', label: '关于' },
]

export default function Nav() {
  const pathname = usePathname()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <nav className="border-b border-gray-700">
      <div className="flex items-center justify-between px-6 py-4">
        <Link href="/" className="text-xl font-bold tracking-tight">
          NucPot <span className="text-blue-400 text-sm font-normal">核材料势函数库</span>
        </Link>
        {/* Desktop nav */}
        <div className="hidden md:flex gap-6 text-sm">
          {NAV_LINKS.map(link => (
            <Link
              key={link.href}
              href={link.href}
              className={pathname === link.href ? 'text-blue-400' : 'hover:text-blue-400 transition'}
            >
              {link.label}
            </Link>
          ))}
        </div>
        {/* Mobile hamburger */}
        <button
          className="md:hidden text-white"
          onClick={() => setMobileOpen(prev => !prev)}
          aria-label="Toggle navigation menu"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
      </div>
      {/* Mobile menu */}
      {mobileOpen && (
        <div className="md:hidden px-6 pb-4 flex flex-col gap-3 text-sm border-t border-gray-700/50">
          {NAV_LINKS.map(link => (
            <Link
              key={link.href}
              href={link.href}
              onClick={() => setMobileOpen(false)}
              className={pathname === link.href ? 'text-blue-400' : 'text-gray-300 hover:text-blue-400 transition'}
            >
              {link.label}
            </Link>
          ))}
        </div>
      )}
    </nav>
  )
}
