'use client'

import { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useAuth } from '@/components/AuthProvider'

const NAV_LINKS = [
  { href: '/browse', label: '浏览' },
  { href: '/materials', label: '材料库' },
  { href: '/ontology', label: '本体' },
  { href: '/literature', label: '文献管理' },
  { href: '/search', label: '高级检索' },
  { href: '/compare', label: '对比' },
  { href: '/feedback', label: '反馈' },
  { href: '/about', label: '关于' },
  { href: '/blog', label: '博客' },
]

const KG_LINKS = [
  { href: '/kg/explore', label: '图谱浏览' },
  { href: '/kg/search', label: 'KG 搜索' },
]

function isKgActive(pathname: string): boolean {
  return pathname.startsWith('/kg')
}

function isKgLinkActive(pathname: string, href: string): boolean {
  return pathname === href
}

export default function Nav() {
  const pathname = usePathname()
  const router = useRouter()
  const { user, loading, signOut } = useAuth()

  const [mobileOpen, setMobileOpen] = useState(false)
  const [kgMobileOpen, setKgMobileOpen] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [kgDropdownOpen, setKgDropdownOpen] = useState(false)

  const dropdownRef = useRef<HTMLDivElement>(null)
  const kgDropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdowns when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
      if (kgDropdownRef.current && !kgDropdownRef.current.contains(e.target as Node)) {
        setKgDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  async function handleSignOut() {
    setDropdownOpen(false)
    setKgDropdownOpen(false)
    setMobileOpen(false)
    setKgMobileOpen(false)
    await signOut()
    router.push('/')
    router.refresh()
  }

  const displayName = user?.username ?? "用户"
  const isAdmin = user?.blog_role === "admin"

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

          {/* KG dropdown */}
          <div className="relative" ref={kgDropdownRef}>
            <button
              onClick={() => setKgDropdownOpen(prev => !prev)}
              className={`flex items-center gap-1 transition ${isKgActive(pathname) ? 'text-blue-400' : 'hover:text-blue-400'}`}
              aria-expanded={kgDropdownOpen}
              aria-haspopup="true"
            >
              知识图谱
              <svg
                className={`w-3.5 h-3.5 transition-transform ${kgDropdownOpen ? 'rotate-180' : ''}`}
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {kgDropdownOpen && (
              <div className="absolute left-0 mt-2 w-36 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 py-1 text-sm">
                {KG_LINKS.map(link => (
                  <Link
                    key={link.href}
                    href={link.href}
                    onClick={() => setKgDropdownOpen(false)}
                    aria-current={isKgLinkActive(pathname, link.href) ? 'page' : undefined}
                    className={`block px-4 py-2 hover:bg-gray-700/60 transition ${isKgLinkActive(pathname, link.href) ? 'text-blue-400' : 'text-gray-200 hover:text-white'}`}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            )}
          </div>

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
                      <Link
                        href="/review/kg"
                        onClick={() => setDropdownOpen(false)}
                        className="block px-4 py-2 hover:bg-gray-700/60 text-gray-200 hover:text-white transition"
                      >
                        审核队列
                      </Link>
                      <Link
                        href="/review/conflicts"
                        onClick={() => setDropdownOpen(false)}
                        className="block px-4 py-2 hover:bg-gray-700/60 text-gray-200 hover:text-white transition"
                      >
                        冲突审核
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
                      {isAdmin && (
                        <Link
                          href="/admin/verify"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          验证管理
                        </Link>
                      )}
                      {isAdmin && (
                        <Link
                          href="/admin/references"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          参考值管理
                        </Link>
                      )}
                      {isAdmin && (
                        <Link
                          href="/admin/v4-extraction/submit"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          V4 提取系统
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

          {/* Mobile KG sub-menu */}
          <button
            onClick={() => setKgMobileOpen(prev => !prev)}
            className={`flex items-center gap-1 text-left transition ${isKgActive(pathname) ? 'text-blue-400' : 'text-gray-300 hover:text-blue-400'}`}
            aria-expanded={kgMobileOpen}
            aria-haspopup="true"
          >
            知识图谱
            <svg
              className={`w-3.5 h-3.5 transition-transform ${kgMobileOpen ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {kgMobileOpen && (
            <div className="ml-4 flex flex-col gap-2">
              {KG_LINKS.map(link => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => { setMobileOpen(false); setKgMobileOpen(false) }}
                  className={isKgLinkActive(pathname, link.href) ? 'text-blue-400' : 'text-gray-400 hover:text-blue-400 transition'}
                >
                  {link.label}
                </Link>
              ))}
            </div>
          )}

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
                  <Link
                    href="/review/kg"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-300 hover:text-blue-400 transition"
                  >
                    审核队列
                  </Link>
                  <Link
                    href="/review/conflicts"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-300 hover:text-blue-400 transition"
                  >
                    冲突审核
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
                  {isAdmin && (
                    <Link
                      href="/admin/verify"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      验证管理
                    </Link>
                  )}
                  {isAdmin && (
                    <Link
                      href="/admin/references"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      参考值管理
                    </Link>
                  )}
                  {isAdmin && (
                    <Link
                      href="/admin/v4-extraction/submit"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      V4 提取系统
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
