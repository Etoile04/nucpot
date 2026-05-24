import { chromium } from 'playwright'
import { mkdirSync } from 'fs'

const dir = 'screenshots'
mkdirSync(dir, { recursive: true })

const pages = [
  { url: 'http://localhost:3000', name: 'home', label: '首页' },
  { url: 'http://localhost:3000/browse', name: 'browse', label: '浏览页' },
  { url: 'http://localhost:3000/search', name: 'search', label: '高级检索页' },
  { url: 'http://localhost:3000/about', name: 'about', label: '关于页' },
]

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  
  for (const p of pages) {
    const page = await context.newPage()
    await page.goto(p.url, { waitUntil: 'networkidle', timeout: 10000 })
    await page.screenshot({ path: `${dir}/${p.name}.png`, fullPage: true })
    console.log(`✅ ${p.label}: ${p.name}.png`)
    await page.close()
  }
  
  // Detail page - need to get a valid ID from API
  const page = await context.newPage()
  const res = await page.goto('http://localhost:3000/api/potentials?limit=1')
  const data = await res.json()
  if (data.potentials && data.potentials.length > 0) {
    const id = data.potentials[0].id
    await page.goto(`http://localhost:3000/potential/${id}`, { waitUntil: 'networkidle', timeout: 10000 })
    await page.screenshot({ path: `${dir}/detail.png`, fullPage: true })
    console.log(`✅ 详情页: detail.png`)
  }
  
  await browser.close()
}

main().catch(e => { console.error(e); process.exit(1) })
