/**
 * One-off DOM snapshot for script-generation bootstrap (same pipeline as saveCurrentDom in util.js).
 * Env:
 *   BOOTSTRAP_URL (required) — page to open
 *   BOOTSTRAP_OUT_DIR (required) — directory for current-dom.json
 */
import fs from 'fs'
import path from 'path'
import { chromium } from 'playwright'
import { saveCurrentDom, injectSmoothCursor } from './util.js'

const url = (process.env.BOOTSTRAP_URL || '').trim()
const outDir = (process.env.BOOTSTRAP_OUT_DIR || '').trim()

if (!url || !outDir) {
  console.error('bootstrap-dom-capture: BOOTSTRAP_URL and BOOTSTRAP_OUT_DIR are required')
  process.exit(1)
}

fs.mkdirSync(outDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } })
  await page
    .goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    .catch(() => page.goto(url, { timeout: 60000 }))
  await injectSmoothCursor(page)
  const testInfo = { outputDir: outDir }
  await saveCurrentDom(page, testInfo, { captureScreenshot: false })
} finally {
  await browser.close()
}

const outFile = path.join(outDir, 'current-dom.json')
if (!fs.existsSync(outFile)) {
  console.error('bootstrap-dom-capture: current-dom.json was not written')
  process.exit(1)
}

process.exit(0)
