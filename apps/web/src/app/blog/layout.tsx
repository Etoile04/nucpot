/**
 * Route layout for /blog/*.
 *
 * Wraps the page content with a `<div class="blog-page-mount">` marker so
 * that the scoped CSS rules in `blog.css` (`body:has(.blog-page-mount) …`)
 * only override the global app-shell `body { overflow: hidden }` /
 * `<main { overflow-y: auto }>` lock while a blog page is being rendered.
 * Other routes (e.g. /browse, /materials, /compare) keep their original
 * scroll behaviour.
 */

import "./blog.css"

export default function BlogLayout({
  children,
}: {
  readonly children: React.ReactNode
}) {
  return <div className="blog-page-mount">{children}</div>
}
