'use client'

import { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

const NAV_LINKS = [
  { href: '/browse', label: '浏览' },
  { href: '/search', label: '高级检索' },
  { href: '/compare', label: '对比' },
  { href: '/about', label: '关于' },
]

export default function Nav() {
  const pathname = usePathname()
  const router = useRouter()
  const { user, profile, loading, signOut } = useAuth()

  const [mobileOpen, setMobileOpen] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  async function handleSignOut() {
    setDropdownOpen(false)
    setMobileOpen(false)
    await signOut()
    router.push('/')
    router.refresh()
  }

  const displayName = profile?.username ?? user?.email?.split('@')[0] ?? '用户'
  const isAdmin = profile?.role === 'admin'

  return (
    <nav className="border-b border-gray-700">
      <div className="flex items-center justify-between px-6 py-4">
        <Link href="/" className="text-xl font-bold tracking-tight">
          NucPot <span className="text-blue-400 text-sm font-normal">核材料势函数库</span>
        </Link>

        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-6 text-sm">
          {NAV_LINKS.map(link => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={pathname === link.href ? 'page' : undefined}
              className={pathname === link.href ? 'text-blue-400' : 'hover:text-blue-400 transition'}
            >
              {link.label}
            </Link>
          ))}

          {/* Auth section */}
          {!loading && (
            <>
              {!user ? (
                <Link
                  href="/login"
                  className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition"
                >
                  登录
                </Link>
              ) : (
                <div className="relative" ref={dropdownRef}>
                  <button
                    onClick={() => setDropdownOpen(prev => !prev)}
                    className="flex items-center gap-2 text-gray-300 hover:text-white transition"
                  >
                    {/* Avatar circle */}
                    <span className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold uppercase select-none">
                      {displayName[0]}
                    </span>
                    <span>{displayName}</span>
                    <svg
                      className={`w-3.5 h-3.5 transition-transform ${dropdownOpen ? 'rotate-180' : ''}`}
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>

                  {dropdownOpen && (
                    <div className="absolute right-0 mt-2 w-44 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 py-1 text-sm">
                      <Link
                        href="/profile"
                        onClick={() => setDropdownOpen(false)}
                        className="block px-4 py-2 hover:bg-gray-700/60 text-gray-200 hover:text-white transition"
                      >
                        个人主页
                      </Link>
                      <Link
                        href="/upload"
                        onClick={() => setDropdownOpen(false)}
                        className="block px-4 py-2 hover:bg-gray-700/60 text-gray-200 hover:text-white transition"
                      >
                        上传势函数
                      </Link>
                      {isAdmin && (
                        <Link
                          href="/admin"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          管理后台
                        </Link>
                      )}
                      <div className="border-t border-gray-700 my-1" />
                      <button
                        onClick={handleSignOut}
                        className="w-full text-left px-4 py-2 hover:bg-gray-700/60 text-red-400 hover:text-red-300 transition"
                      >
                        退出登录
                      </button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Mobile hamburger */}
        <button
          className="md:hidden text-white"
          onClick={() => setMobileOpen(prev => !prev)}
          aria-label="打开导航菜单"
          aria-expanded={mobileOpen}
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

          {/* Mobile auth */}
          {!loading && (
            <>
              {!user ? (
                <Link
                  href="/login"
                  onClick={() => setMobileOpen(false)}
                  className="text-blue-400 hover:text-blue-300 transition font-medium"
                >
                  登录 / 注册
                </Link>
              ) : (
                <>
                  <div className="border-t border-gray-700/50 pt-3 text-gray-400 text-xs">
                    {displayName}
                  </div>
                  <Link
                    href="/profile"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-300 hover:text-blue-400 transition"
                  >
                    个人主页
                  </Link>
                  <Link
                    href="/upload"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-300 hover:text-blue-400 transition"
                  >
                    上传势函数
                  </Link>
                  {isAdmin && (
                    <Link
                      href="/admin"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      管理后台
                    </Link>
                  )}
                  <button
                    onClick={handleSignOut}
                    className="text-left text-red-400 hover:text-red-300 transition"
                  >
                    退出登录
                  </button>
                </>
              )}
            </>
          )}
        </div>
      )}
    </nav>
  )
}
