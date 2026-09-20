'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { App } from 'antd'
import { useAuth } from '@/components/AuthProvider'
import { SessionIndicator, SessionTimerBadge, useExpiringSoonToast } from '@/components/session'

// NFM-4990 (IA-REFACTOR P1, NFM-4984 裁决定稿站点地图): first level =
// 势函数列表 / 材料体系 / 文献库 / 关于. Everything that used to be a
// first-level entry (检索, 对比, 本体, KG, 博客, 反馈) moves into the
// 「更多」dropdown — functionality preserved, just no longer first-level.
// Benchmark/数据集/API 文档 blocks are unbuilt in P1 and intentionally
// NOT rendered (P2 will enable them).
const PRIMARY_LINKS = [
  { href: '/potentials', label: '势函数列表' },
  { href: '/materials', label: '材料体系' },
  { href: '/publications', label: '文献库' },
  { href: '/about', label: '关于' },
]

const MORE_LINKS = [
  { href: '/potentials/search', label: '势函数检索' },
  { href: '/potentials/compare', label: '势函数对比' },
  { href: '/kg/explore', label: '图谱浏览' },
  { href: '/kg/search', label: 'KG 搜索' },
  { href: '/ontology', label: '本体' },
  { href: '/blog', label: '博客' },
  { href: '/feedback', label: '反馈' },
]

function isMoreActive(pathname: string): boolean {
  return MORE_LINKS.some((link) => pathname.startsWith(link.href))
}

function isMoreLinkActive(pathname: string, href: string): boolean {
  return pathname === href
}

export default function Nav() {
  const pathname = usePathname()
  const router = useRouter()
  const { user, loading, signOut } = useAuth()

  const [mobileOpen, setMobileOpen] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [moreDropdownOpen, setMoreDropdownOpen] = useState(false)
  const [moreMobileOpen, setMoreMobileOpen] = useState(false)

  const dropdownRef = useRef<HTMLDivElement>(null)
  const moreDropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdowns when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
      if (moreDropdownRef.current && !moreDropdownRef.current.contains(e.target as Node)) {
        setMoreDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  async function handleSignOut() {
    setDropdownOpen(false)
    setMoreDropdownOpen(false)
    setMobileOpen(false)
    setMoreMobileOpen(false)
    await signOut()
    router.push('/')
    router.refresh()
  }

  const displayName = user?.username ?? "用户"
  const isAdmin = user?.blog_role === "admin"

  // NFM-2417: show warning toast when session is about to expire.
  const { message } = App.useApp()
  const handleExpiringSoon = useCallback(() => {
    message.warning({
      content: "您的会话即将过期，请保存工作。",
      duration: 10,
    })
  }, [message])
  useExpiringSoonToast(handleExpiringSoon)

  return (
    <nav className="border-b border-gray-700">
      <div className="flex items-center justify-between px-6 py-4">
        <Link href="/" className="text-xl font-bold tracking-tight focus-visible:!outline focus-visible:!outline-2 focus-visible:!outline-blue-500 focus-visible:!outline-offset-2">
          NucPot <span className="text-blue-400 text-sm font-normal">核材料势函数库</span>
        </Link>

        {/* Desktop nav — lg (1024px) keeps the CJK labels comfortably on
            one line even with the 更多 dropdown open. */}
        <div className="hidden lg:flex items-center gap-6 text-sm">
          {PRIMARY_LINKS.map(link => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={pathname === link.href ? 'page' : undefined}
              // NFM-3794 a11y:
              // 1. Inactive links need an explicit base color — without
              //    `text-gray-200`, antd's `colorLink: #1668dc` leaks through
              //    via the `:where(.css-plsjn) a` selector (3.42:1 vs the dark
              //    nav bg — fails WCAG AA). Tailwind's `text-gray-200` is
              //    specificity (0,1,0), but antd's rule sits outside
              //    `@layer utilities` so it wins the cascade. We add the
              //    `!` important modifier to force the override.
              // 2. `inline-flex items-center py-2` keeps each link ≥24px tall
              //    (WCAG 2.5.5 / Lighthouse target-size). `inline-flex` is
              //    required because <a> is `display:inline` by default and
              //    vertical padding on inline elements does NOT expand the
              //    layout box — axe-core measures 17px height for plain
              //    `py-2`, failing the 24×24 minimum.
              //    (Tailwind v4 config in this repo only emits integer
              //    `py-*` classes — `py-1.5` would be ignored.)
              className={
                pathname === link.href
                  ? 'inline-flex items-center !text-blue-400 py-2'
                  : 'inline-flex items-center !text-gray-200 hover:!text-blue-400 transition py-2'
              }
            >
              {link.label}
            </Link>
          ))}

          {/* 更多 dropdown — retired first-level destinations (NFM-4990) */}
          <div className="relative" ref={moreDropdownRef}>
            <button
              onClick={() => setMoreDropdownOpen(prev => !prev)}
              // NFM-3794 a11y: see desktop link note above — inactive
              // dropdown trigger needs a base color too. `py-2` ensures
              // ≥24px tall.
              className={`flex items-center gap-1 transition py-2 ${isMoreActive(pathname) ? '!text-blue-400' : '!text-gray-200 hover:!text-blue-400'}`}
              aria-expanded={moreDropdownOpen}
              aria-haspopup="true"
            >
              更多
              <svg
                className={`w-3.5 h-3.5 transition-transform ${moreDropdownOpen ? 'rotate-180' : ''}`}
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {moreDropdownOpen && (
              // NFM-5000: right-anchor so the panel opens inward — trigger sits
              // next to 登录/注册, left-0 pushed it 6px past a 1440px viewport.
              <div className="absolute right-0 mt-2 w-40 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 py-1 text-sm">
                {MORE_LINKS.map(link => (
                  <Link
                    key={link.href}
                    href={link.href}
                    onClick={() => setMoreDropdownOpen(false)}
                    aria-current={isMoreLinkActive(pathname, link.href) ? 'page' : undefined}
                    className={`block px-4 py-2 hover:bg-gray-700/60 transition ${isMoreLinkActive(pathname, link.href) ? 'text-blue-400' : 'text-gray-200 hover:text-white'}`}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            )}
          </div>

          {/* Session indicator — right of nav links, left of auth.
              * Hidden on mobile (lg:flex wraps the entire desktop nav, so the
              * indicator is implicitly hidden on small viewports per spec §2.1).
              * Spec: [NFM-2251](/NFM/issues/NFM-2251) §2.6 */}
          <SessionIndicator />

          {/* Auth section */}
          {!loading && (
            <>
              {!user ? (
                <Link
                  href="/login"
                  // NFM-3794 a11y: antd's `:where(.css-plsjn) a` reset
                  // overrides both `text-white` AND `bg-blue-600` to
                  // colorLink #1668dc on transparent — 3.42:1 on the #101828
                  // header. `!text-white !bg-blue-600` forces override.
                  className="px-4 py-1.5 rounded-lg !bg-blue-600 hover:!bg-blue-500 !text-white font-medium transition"
                >
                  登录
                </Link>
              ) : (
                <div className="relative" ref={dropdownRef}>
                  <button
                    onClick={() => setDropdownOpen(prev => !prev)}
                    className="flex items-center gap-2 text-gray-200 hover:text-white transition"
                  >
                    {/* Avatar circle */}
                    <span className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold uppercase select-none">
                      {displayName[0]}
                    </span>
                    <span>{displayName}</span>
                    <SessionTimerBadge className="ml-1" />
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
                      {isAdmin && (
                        <Link
                          href="/admin/lightrag"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          知识图谱管理
                        </Link>
                      )}
                      {isAdmin && (
                        <Link
                          href="/admin/ontology"
                          onClick={() => setDropdownOpen(false)}
                          className="block px-4 py-2 hover:bg-gray-700/60 text-yellow-400 hover:text-yellow-300 transition"
                        >
                          本体版本管理
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

        {/* Mobile hamburger — visible below the lg (1024px) breakpoint. */}
        <button
          // NFM-3794 a11y: target-size. The hamburger has a 24×24 SVG inside,
          // and we need ≥24×24 layout box at the audit viewport. `inline-flex
          // items-center justify-center` plus `p-2` gives a 40×40 clickable
          // box. `min-w-[44px] min-h-[44px]` is belt-and-suspenders — even if
          // axe-core measures this element while `lg:hidden` is in effect,
          // the box will be at least 44×44. The `lg:hidden` rule is still
          // present so the button doesn't render at desktop sizes.
          className="lg:hidden inline-flex items-center justify-center min-w-[44px] min-h-[44px] text-white p-2 rounded hover:bg-gray-800/60 transition"
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
        <div className="lg:hidden px-6 pb-4 flex flex-col gap-3 text-sm border-t border-gray-700/50">
          {PRIMARY_LINKS.map(link => (
            <Link
              key={link.href}
              href={link.href}
              onClick={() => setMobileOpen(false)}
              className={pathname === link.href ? 'text-blue-400' : 'text-gray-200 hover:text-blue-400 transition'}
            >
              {link.label}
            </Link>
          ))}

          {/* Mobile 更多 sub-menu */}
          <button
            onClick={() => setMoreMobileOpen(prev => !prev)}
            className={`flex items-center gap-1 text-left transition ${isMoreActive(pathname) ? 'text-blue-400' : 'text-gray-200 hover:text-blue-400'}`}
            aria-expanded={moreMobileOpen}
            aria-haspopup="true"
          >
            更多
            <svg
              className={`w-3.5 h-3.5 transition-transform ${moreMobileOpen ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {moreMobileOpen && (
            <div className="ml-4 flex flex-col gap-2">
              {MORE_LINKS.map(link => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => { setMobileOpen(false); setMoreMobileOpen(false) }}
                  className={isMoreLinkActive(pathname, link.href) ? 'text-blue-400' : 'text-gray-400 hover:text-blue-400 transition'}
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
                  // NFM-3794 a11y: same antd specificity override as the
                  // desktop login button — `text-blue-400` is lost to
                  // colorLink #1668dc without `!`.
                  className="!text-blue-400 hover:!text-blue-300 transition font-medium"
                >
                  登录 / 注册
                </Link>
              ) : (
                <>
                  <div className="border-t border-gray-700/50 pt-3 text-gray-400 text-xs">
                    {displayName}
                    <SessionTimerBadge className="ml-2" />
                  </div>
                  <Link
                    href="/profile"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-200 hover:text-blue-400 transition"
                  >
                    个人主页
                  </Link>
                  <Link
                    href="/upload"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-200 hover:text-blue-400 transition"
                  >
                    上传势函数
                  </Link>
                  <Link
                    href="/review/kg"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-200 hover:text-blue-400 transition"
                  >
                    审核队列
                  </Link>
                  <Link
                    href="/review/conflicts"
                    onClick={() => setMobileOpen(false)}
                    className="text-gray-200 hover:text-blue-400 transition"
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
                  {isAdmin && (
                    <Link
                      href="/admin/lightrag"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      知识图谱管理
                    </Link>
                  )}
                  {isAdmin && (
                    <Link
                      href="/admin/ontology"
                      onClick={() => setMobileOpen(false)}
                      className="text-yellow-400 hover:text-yellow-300 transition"
                    >
                      本体版本管理
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
