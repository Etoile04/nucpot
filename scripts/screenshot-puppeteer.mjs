import puppeteer from 'puppeteer'
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
  const browser = await puppeteer.launch({ headless: true })
  const viewPort = { width: 1280, height: 800 }

  for (const p of pages) {
    const page = await browser.newPage()
    await page.setViewport(viewPort)
    await page.goto(p.url, { waitUntil: 'networkidle2', timeout: 10000 })
    await page.screenshot({ path: `${dir}/${p.name}.png`, fullPage: true })
    console.log(`✅ ${p.label}: ${p.name}.png`)
    await page.close()
  }

  // Detail page
  const apiPage = await browser.newPage()
  const res = await apiPage.goto('http://localhost:3000/api/potentials?limit=1')
  const data = JSON.parse(await res.text())
  if (data.potentials && data.potentials.length > 0) {
    const id = data.potentials[0].id
    const page = await browser.newPage()
    await page.setViewport(viewPort)
    await page.goto(`http://localhost:3000/potential/${id}`, { waitUntil: 'networkidle2', timeout: 10000 })
    await page.screenshot({ path: `${dir}/detail.png`, fullPage: true })
    console.log(`✅ 详情页: detail.png`)
    await page.close()
  }
  await apiPage.close()

  await browser.close()
  console.log('\nDone!')
}

main().catch(e => { console.error(e); process.exit(1) })
