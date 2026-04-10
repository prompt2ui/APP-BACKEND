// src/test/util.js
import fs from 'fs'
import path from 'path'
import { JSDOM } from 'jsdom'

function cleanHTML(html) {
  const dom = new JSDOM(html)
  const doc = dom.window.document
  doc.querySelectorAll('script, style, noscript').forEach((el) => el.remove())
  const importantAttrs = new Set([
    'type', 'name', 'id', 'placeholder', 'required',
    'aria-label', 'aria-labelledby', 'aria-controls', 'aria-expanded', 'aria-haspopup',
    'aria-modal', 'aria-selected', 'role', 'for', 'href', 'value', 'data-testid',
  ])
  doc.querySelectorAll('*').forEach((tag) => {
    for (const attr of Array.from(tag.attributes)) {
      if (!importantAttrs.has(attr.name)) tag.removeAttribute(attr.name)
    }
  })
  return doc.documentElement.outerHTML.replace(/\s+/g, ' ').replace(/>\s+</g, '><').trim()
}

// Internal helper: grouped UI snapshot (not exported)
async function scanVisibleUIGroupedByContainer(page, limit = 24) {
  try {
    return await page.evaluate((limit) => {
      const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim()
      const isVisible = (el) => {
        if (!(el instanceof Element)) return false
        const style = window.getComputedStyle(el)
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false
        if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true') return false
        const rect = el.getBoundingClientRect()
        if (rect.width > 0 && rect.height > 0) return true
        // sr-only radio/checkbox: width/height = 0, but parent <label> is visible
        const tag = (el.tagName || '').toLowerCase()
        if (tag === 'input' && ['radio', 'checkbox'].includes((el.type || '').toLowerCase())) {
          const parentLabel = el.closest('label')
          if (parentLabel) {
            const pr = parentLabel.getBoundingClientRect()
            return pr.width > 0 && pr.height > 0
          }
        }
        return false
      }
      const getName = (el) => {
        // For sr-only radio/checkbox, prefer parent label text
        const tag = (el.tagName || '').toLowerCase()
        if (tag === 'input' && ['radio', 'checkbox'].includes((el.type || '').toLowerCase())) {
          const parentLabel = el.closest('label')
          if (parentLabel) {
            const txt = normalize((parentLabel.innerText || parentLabel.textContent || '').trim())
            if (txt) return txt
          }
          return normalize(el.value || el.name || el.id || '')
        }
        return normalize(
          el.getAttribute('aria-label') ||
          el.getAttribute('aria-labelledby') ||
          el.innerText ||
          el.textContent ||
          el.getAttribute('placeholder') ||
          el.getAttribute('name') ||
          el.getAttribute('id') ||
          ''
        )
      }
      const getSelectorHints = (el) => ({
        id: el.getAttribute('id') || '',
        name: el.getAttribute('name') || '',
        placeholder: el.getAttribute('placeholder') || '',
        aria_label: el.getAttribute('aria-label') || '',
        data_testid: el.getAttribute('data-testid') || '',
      })
      const getRole = (el) => {
        const role = (el.getAttribute('role') || '').toLowerCase()
        if (role) return role
        const tag = (el.tagName || '').toLowerCase()
        if (tag === 'button' || (tag === 'input' && ['button', 'submit'].includes((el.type || '').toLowerCase()))) return 'button'
        if (tag === 'select') return 'combobox'
        if (tag === 'a' && el.href) return 'link'
        if (tag === 'input') {
          const type = (el.type || '').toLowerCase()
          if (type === 'radio') return 'radio'
          if (type === 'checkbox') return 'checkbox'
          return 'textbox'
        }
        if (tag === 'textarea') return 'textbox'
        if (tag === 'dialog') return 'dialog'
        if (tag === 'option') return 'option'
        return ''
      }
      const isClickableCardRoot = (el) => {
        const tag = (el.tagName || '').toLowerCase()
        if (!['div', 'article', 'li', 'section'].includes(tag)) return false
        const tid = (el.getAttribute('data-testid') || '').trim()
        if (!tid) return false
        if (tid === 'product-card' || tid === 'item-card') return true
        if (/-card$/i.test(tid) && !/(thumb|image|preview)$/i.test(tid)) return true
        return false
      }
      const labelForClickableCard = (el) => {
        const pick = el.querySelector?.('[data-testid="product-name"], [data-testid="title"], h1, h2, h3')
        if (pick) {
          const t = normalize((pick.innerText || pick.textContent || '').trim())
          if (t) return t.slice(0, 120)
        }
        const t = normalize((el.innerText || el.textContent || '').trim())
        return t.slice(0, 120)
      }
      const classify = (el) => {
        if (isClickableCardRoot(el)) return 'clickable_cards'
        const role = getRole(el)
        const tag = (el.tagName || '').toLowerCase()
        const type = (el.type || '').toLowerCase()
        if (role === 'combobox' || tag === 'select') return 'dropdowns'
        if (role === 'option' || role === 'menuitem' || tag === 'option') return 'options'
        // radio and checkbox go in their own bucket so LLM sees them clearly
        if (tag === 'input' && (type === 'radio' || type === 'checkbox')) return 'options'
        if (role === 'link' || (tag === 'a' && el.href)) return 'links'
        if ((tag === 'input' && !['hidden', 'button', 'submit'].includes(type)) || tag === 'textarea') return 'inputs'
        if (role === 'button' || tag === 'button' || (tag === 'input' && ['button', 'submit'].includes(type))) return 'buttons'
        return ''
      }
      const groupFor = (el) => {
        const c = el.closest('dialog, [role="dialog"], [role="alertdialog"], form, table, main')
        if (!c) return { key: 'root', label: 'Main content', role: 'main', tag: 'body' }
        return {
          key: c.id || `${(c.tagName || 'container').toLowerCase()}-${Math.abs((c.outerHTML || '').length % 100000)}`,
          label: getName(c),
          role: c.getAttribute('role') || '',
          tag: (c.tagName || '').toLowerCase(),
        }
      }
      const empty = () => ({
        buttons: [],
        inputs: [],
        dropdowns: [],
        options: [],
        links: [],
        clickable_cards: [],
      })
      const out = {}
      const keyTexts = []
      const all = document.querySelectorAll(
        'button, a[href], input, select, textarea, option, [role="button"], [role="link"], [role="combobox"], [role="textbox"], [role="searchbox"], [role="option"], [role="menuitem"], [data-testid="product-card"], [data-testid="item-card"], [data-testid$="-card"]'
      )
      const seen = new Set()
      for (const el of all) {
        const tag = (el.tagName || '').toLowerCase()
        const type = (el.type || '').toLowerCase()

        // Skip <option> elements — they will be grouped under their parent <select>
        if (tag === 'option') continue

        if (!isVisible(el)) continue
        const cat = classify(el)
        if (!cat) continue
        if (cat === 'clickable_cards' && !isClickableCardRoot(el)) continue
        const g = groupFor(el)
        if (!out[g.key]) out[g.key] = { label: g.label, role: g.role, tag: g.tag, ...empty() }
        const hints = getSelectorHints(el)
        const isCard = cat === 'clickable_cards'
        const item = {
          label: isCard ? labelForClickableCard(el) : getName(el),
          role: isCard ? 'clickable-card' : getRole(el),
          tag: tag,
          id: hints.id,
          name: hints.name,
          placeholder: hints.placeholder,
          aria_label: hints.aria_label,
          data_testid: hints.data_testid,
          text: normalize((el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()).slice(0, 80),
        }
        if (isCard && hints.data_testid) {
          item.interaction_hint =
            `Whole tile is clickable — use page.getByTestId('${hints.data_testid}').first() or .nth(i); optional: .filter({ hasText: '...' })`
        }

        // --- sr-only detection for radio/checkbox ---
        if (tag === 'input' && (type === 'radio' || type === 'checkbox')) {
          const rect = el.getBoundingClientRect()
          const isSrOnly = (rect.width === 0 || rect.height === 0) || el.classList.contains('sr-only')
          if (isSrOnly) {
            item.sr_only = true
            item.interaction_hint = 'HIDDEN input — use page.getByLabel(\'' + item.label + '\').click() or page.getByText(\'' + item.label + '\').click() instead of .check()'
          }
        }

        // --- For <select>, inline its <option> children ---
        if (tag === 'select') {
          const opts = Array.from(el.querySelectorAll('option'))
          item.select_options = opts.map(o => ({
            value: o.value || '',
            text: normalize((o.innerText || o.textContent || '').trim()),
            selected: o.selected,
          })).filter(o => o.value !== '') // skip placeholder option
        }

        const sig = `${g.key}|${cat}|${item.role}|${item.tag}|${item.label}`
        if (seen.has(sig)) continue
        seen.add(sig)
        if (out[g.key][cat].length < limit) out[g.key][cat].push(item)
      }

      // Capture visible non-interactive texts that are useful for assertions
      const textSelector = 'h1,h2,h3,h4,h5,h6,p,span,li,td,th'
      const textSeen = new Set()
      document.querySelectorAll(textSelector).forEach((el) => {
        if (!isVisible(el)) return
        const txt = normalize(el.innerText || el.textContent || '')
        if (!txt || txt.length < 3) return
        if (txt.length > 140) return
        if (textSeen.has(txt)) return
        textSeen.add(txt)
        keyTexts.push(txt)
      })
      if (!out.root) out.root = { label: 'Main content', role: 'main', tag: 'body', ...empty() }
      out.root.texts = keyTexts.slice(0, Math.max(12, limit))
      return out
    }, limit)
  } catch (error) {
    return {}
  }
}

const __uiEventSinkState = new WeakMap()

function sortLogsByTimestamp(logs) {
  return [...(logs || [])].sort(
    (a, b) => new Date(a?.timestamp || 0).getTime() - new Date(b?.timestamp || 0).getTime()
  )
}

/**
 * Push UI + navigation events (click, input, change, submit, navigated, document request)
 * into one or more log arrays. Safe to call from both `setupEventListener` and
 * `setupConsoleLogging` on the same page: listeners are registered once, events fan out.
 */
async function registerUiEventSink(page, logs) {
  let rec = __uiEventSinkState.get(page)
  if (!rec) {
    rec = { sinks: new Set(), attached: false }
    __uiEventSinkState.set(page, rec)
  }
  rec.sinks.add(logs)

  const broadcast = (entry) => {
    const row = { ...entry, timestamp: new Date().toISOString() }
    for (const sink of rec.sinks) sink.push(row)
  }

  if (rec.attached) return
  rec.attached = true

  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) {
      broadcast({ type: 'navigated', url: frame.url() })
    }
  })

  page.on('request', (request) => {
    const resourceType = request.resourceType()
    if (resourceType === 'document') {
      broadcast({
        type: 'request',
        method: request.method(),
        url: request.url(),
        resourceType,
      })
    }
  })

  try {
    await page.exposeFunction('__agentRecordUiEvent', (event) => {
      broadcast({ ...event })
    })
  } catch (_) {
    // already registered in reused context
  }

  await page.addInitScript(() => {
    if (window.__agentUiEventsReady) return
    window.__agentUiEventsReady = true
    const pick = (el) =>
      (el?.getAttribute?.('aria-label') ||
        el?.getAttribute?.('placeholder') ||
        el?.innerText ||
        el?.textContent ||
        el?.id ||
        el?.name ||
        '').replace(/\s+/g, ' ').trim().slice(0, 120)
    const send = (payload) => {
      if (window.__agentRecordUiEvent) {
        window.__agentRecordUiEvent(payload).catch(() => { })
      }
    }
    const MAX_VAL = 240
    const sliceVal = (s) => {
      const t = String(s ?? '')
      if (t.length <= MAX_VAL) return t
      return `${t.slice(0, MAX_VAL - 1)}…`
    }
    const fieldValueForLog = (el) => {
      if (!el?.tagName) return { value: '', valueRedacted: false }
      const tag = el.tagName.toLowerCase()
      const type = String(el.getAttribute?.('type') || '').toLowerCase()
      if (type === 'password') return { value: '[redacted]', valueRedacted: true }
      if (type === 'hidden') return { value: '[hidden]', valueRedacted: false }
      if (type === 'file') {
        const n = el.files?.length || 0
        return { value: n ? `[${n} file(s)]` : '(no file)', valueRedacted: false }
      }
      if (type === 'checkbox' || type === 'radio') {
        return { value: el.checked ? 'checked' : 'unchecked', valueRedacted: false }
      }
      if (tag === 'select') {
        if (el.multiple) {
          const opts = Array.from(el.selectedOptions || [])
            .map((o) => (o.label || o.value || '').trim())
            .filter(Boolean)
          return { value: sliceVal(opts.join(', ')), valueRedacted: false }
        }
        return { value: sliceVal(el.value ?? ''), valueRedacted: false }
      }
      if (tag === 'textarea' || tag === 'input') {
        return { value: sliceVal(el.value ?? ''), valueRedacted: false }
      }
      return { value: '', valueRedacted: false }
    }
    const inputDebounce = new WeakMap()
    const INPUT_DEBOUNCE_MS = 320
    document.addEventListener('click', (e) => {
      const el = e.target
      const tag = el?.tagName?.toLowerCase() || ''
      const base = { type: 'click', tag, label: pick(el) }
      if (tag === 'a') base.href = el?.getAttribute?.('href') || ''
      send(base)
    }, true)
    document.addEventListener('input', (e) => {
      const el = e.target
      const tag = el?.tagName?.toLowerCase() || ''
      if (tag !== 'input' && tag !== 'textarea') return
      const type = String(el.getAttribute?.('type') || '').toLowerCase()
      if (type === 'checkbox' || type === 'radio' || type === 'file' || type === 'hidden') return
      let t = inputDebounce.get(el)
      if (t) clearTimeout(t)
      t = setTimeout(() => {
        const { value, valueRedacted } = fieldValueForLog(el)
        send({ type: 'input', tag, label: pick(el), value, valueRedacted })
        inputDebounce.delete(el)
      }, INPUT_DEBOUNCE_MS)
      inputDebounce.set(el, t)
    }, true)
    document.addEventListener('change', (e) => {
      const el = e.target
      const t = inputDebounce.get(el)
      if (t) {
        clearTimeout(t)
        inputDebounce.delete(el)
      }
      const tag = el?.tagName?.toLowerCase() || ''
      const { value, valueRedacted } = fieldValueForLog(el)
      send({ type: 'change', tag, label: pick(el), value, valueRedacted })
    }, true)
    document.addEventListener('submit', (e) => {
      const el = e.target
      const tag = el?.tagName?.toLowerCase() || ''
      send({
        type: 'submit',
        tag,
        label: pick(el),
        action: typeof el?.action === 'string' ? el.action : '',
        method: String(el?.method || 'get').toUpperCase(),
      })
    }, true)
  })
}

async function setupEventListener(page) {
  const events = []
  const startTime = new Date().toISOString()
  await registerUiEventSink(page, events)
  return { startTime, logs: events }
}

async function saveCurrentDom(page, testInfo, options = {}) {
  const outDir = testInfo.outputDir || path.join(process.cwd(), 'src/test/test-result')
  fs.mkdirSync(outDir, { recursive: true })

  // Give the page time to settle (animations/network/UI updates) before snapshotting.
  await page.waitForTimeout(3000)

  const rawHtml = await page.content()
  const cleaned = cleanHTML(rawHtml)
  const groupedElements = await scanVisibleUIGroupedByContainer(page, options.limit || 24)
  const fileName = String(options.fileName || '').trim()

  // Inject page context so node_execute / LLM knows which URL was captured
  groupedElements._url = page.url()
  groupedElements._page_title = await page.title()

  // Capture first visible heading (h1/h2/h3) — critical for SPA where URL stays the same
  // but the visible view changes (e.g. Step 1 "Personal Information" → Step 2 "Preferences")
  groupedElements._page_heading = await page.evaluate(() => {
    for (const sel of ['h1', 'h2', 'h3']) {
      const els = Array.from(document.querySelectorAll(sel))
      for (const el of els) {
        const style = window.getComputedStyle(el)
        if (style.display === 'none' || style.visibility === 'hidden') continue
        const rect = el.getBoundingClientRect()
        if (rect.width === 0 || rect.height === 0) continue
        const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()
        if (txt.length > 1) return txt
      }
    }
    return ''
  })

  // Filenames are fixed as requested
  const groupedPath = path.join(outDir, fileName ? `${fileName}.json` : 'current-dom.json')
  fs.writeFileSync(groupedPath, JSON.stringify(groupedElements, null, 2))

  if (options.captureScreenshot !== false) {
    const pngName = options.screenshotName || (fileName ? `${fileName}.png` : 'current-ui.png')
    const screenshotPath = path.join(outDir, pngName)
    await page.screenshot({ path: screenshotPath, fullPage: options.fullPage === true })
  }
}

// NOTE: We intentionally do NOT export a separate auto-clean helper.
// Tests should call `saveCurrentDom(page, testInfo, ...)` directly.


// ============================================================
// CURSOR ANIMATION OVERLAY
// ============================================================
async function injectSmoothCursor(page) {
  await page.evaluate(() => {
    document.querySelector('#cursor-overlay')?.remove()

    const cursor = document.createElement('div')
    cursor.id = 'cursor-overlay'
    Object.assign(cursor.style, {
      position: 'fixed',
      width: '18px',
      height: '18px',
      borderRadius: '50%',
      background: 'rgba(255,0,0,0.7)',
      boxShadow: '0 0 6px rgba(255,0,0,0.5)',
      zIndex: '999999',
      pointerEvents: 'none',
      transform: 'translate(-50%, -50%)',
    })
    document.body.appendChild(cursor)

    window.__cursorState = { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    cursor.style.left = window.__cursorState.x + 'px'
    cursor.style.top = window.__cursorState.y + 'px'

    window.__cursorMoveTo = (x, y, duration = 400) => {
      const startX = window.__cursorState.x
      const startY = window.__cursorState.y
      const startTime = performance.now()
      const animate = (now) => {
        const t = Math.min(1, (now - startTime) / duration)
        const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t
        const cx = startX + (x - startX) * ease
        const cy = startY + (y - startY) * ease
        cursor.style.left = cx + 'px'
        cursor.style.top = cy + 'px'
        window.__cursorState.x = cx
        window.__cursorState.y = cy
        if (t < 1) requestAnimationFrame(animate)
      }
      requestAnimationFrame(animate)
    }

    document.addEventListener('click', (e) => {
      const pulse = document.createElement('div')
      Object.assign(pulse.style, {
        position: 'fixed',
        width: '40px',
        height: '40px',
        borderRadius: '50%',
        border: '2px solid red',
        left: e.pageX - 20 + 'px',
        top: e.pageY - 20 + 'px',
        zIndex: '999999',
        pointerEvents: 'none',
        opacity: '0.8',
        transition: 'transform 0.3s ease, opacity 0.3s ease',
      })
      document.body.appendChild(pulse)
      setTimeout(() => {
        pulse.style.transform = 'scale(1.5)'
        pulse.style.opacity = '0'
      }, 10)
      setTimeout(() => pulse.remove(), 350)
    })
  })
}

// ============================================================
// CURSOR MOVEMENT & ELEMENT HIGHLIGHT
// ============================================================
/**
 * Scroll so the target is in frame (centered) before cursor/highlight — avoids off-viewport clicks and cropped recordings.
 */
async function ensureLocatorCenteredInViewport(page, locator, { smooth = true, settleMs = 550 } = {}) {
  await locator.evaluate(
    (el, smoothFlag) => {
      el.scrollIntoView({
        block: 'center',
        inline: 'nearest',
        behavior: smoothFlag ? 'smooth' : 'instant',
      })
    },
    smooth
  )
  await page.waitForTimeout(smooth ? settleMs : 80)
}

async function moveCursorTo(page, locator, duration = 800) {
  await ensureLocatorCenteredInViewport(page, locator)
  const box = await locator.boundingBox()
  if (!box) return
  const centerX = box.x + box.width / 2
  const centerY = box.y + box.height / 2
  await page.evaluate(
    ([x, y, d]) => window.__cursorMoveTo && window.__cursorMoveTo(x, y, d),
    [centerX, centerY, duration]
  )
  await page.waitForTimeout(duration)
}

async function highlightElement(page, locator) {
  await ensureLocatorCenteredInViewport(page, locator, { smooth: false })
  const el = await locator.elementHandle()
  await page.evaluate((el) => {
    const rect = el.getBoundingClientRect()
    const outline = document.createElement('div')
    Object.assign(outline.style, {
      position: 'fixed',
      top: rect.top + 'px',
      left: rect.left + 'px',
      width: rect.width + 'px',
      height: rect.height + 'px',
      border: '2px solid red',
      borderRadius: '4px',
      zIndex: '99998',
      pointerEvents: 'none',
      opacity: '1',
      transition: 'opacity 0.4s ease',
    })
    document.body.appendChild(outline)
    setTimeout(() => (outline.style.opacity = '0'), 400)
    setTimeout(() => outline.remove(), 800)
  }, el)
}



// ============================================================
// CONSOLE LOG COLLECTION
// ============================================================
async function setupConsoleLogging(page) {
  const logs = []
  const startTime = new Date().toISOString()

  page.on('console', (msg) => {
    logs.push({
      type: msg.type(),
      text: msg.text(),
      location: msg.location(),
      timestamp: new Date().toISOString(),
    })
  })

  page.on('pageerror', (err) => {
    logs.push({
      type: 'pageerror',
      text: String(err?.message || err),
      location: {},
      timestamp: new Date().toISOString(),
    })
  })

  await registerUiEventSink(page, logs)

  return {
    startTime,
    logs,
  }
}


// ============================================================
// NETWORK REQUEST COLLECTION (only fetch and xhr)
// ============================================================

/** Aggregate durationMs from captured fetch/xhr *response* events (Playwright-side). */
function summarizeMonitoredXhrFetch(events) {
  const durations = []
  for (const e of events || []) {
    if (e?.type === 'response' && typeof e.durationMs === 'number' && e.durationMs >= 0) {
      durations.push(e.durationMs)
    }
  }
  if (!durations.length) {
    return { count: 0, minMs: null, maxMs: null, avgMs: null, p95Ms: null }
  }
  const sorted = [...durations].sort((a, b) => a - b)
  const n = sorted.length
  const sum = sorted.reduce((a, b) => a + b, 0)
  const p95Idx = Math.min(n - 1, Math.ceil(n * 0.95) - 1)
  return {
    count: n,
    minMs: sorted[0],
    maxMs: sorted[n - 1],
    avgMs: Math.round((sum / n) * 100) / 100,
    p95Ms: sorted[p95Idx],
  }
}

/**
 * @param {import('@playwright/test').Page} page
 * @param {{ performance?: boolean }} [options] — `performance` defaults to true: collect browser timing + merge with xhr/fetch stats.
 */
async function setupNetworkMonitoring(page, options = {}) {
  const performanceEnabled = options.performance !== false
  const events = []
  const startTime = new Date().toISOString()
  const startByRequest = new Map()

  const redactHeaders = (headers) => {
    const out = {}
    const deny = ['authorization', 'cookie', 'set-cookie', 'x-api-key', 'apikey']
    for (const [k, v] of Object.entries(headers || {})) {
      if (deny.includes(String(k).toLowerCase())) {
        out[k] = '[REDACTED]'
      } else {
        out[k] = v
      }
    }
    return out
  }

  page.on('request', (request) => {
    const resourceType = request.resourceType()
    if (resourceType === 'fetch' || resourceType === 'xhr') {
      const now = Date.now()
      startByRequest.set(request, now)
      events.push({
        type: 'request',
        method: request.method(),
        url: request.url(),
        resourceType,
        headers: redactHeaders(request.headers()),
        timestamp: new Date().toISOString(),
      })
    }
  })

  page.on('response', (response) => {
    const request = response.request()
    const resourceType = request.resourceType()
    if (resourceType === 'fetch' || resourceType === 'xhr') {
      const startMs = startByRequest.get(request)
      const durationMs = typeof startMs === 'number' ? Date.now() - startMs : null
      const headers = response.headers()
      events.push({
        type: 'response',
        method: request.method(),
        url: response.url(),
        status: response.status(),
        statusText: response.statusText(),
        resourceType,
        headers: redactHeaders(headers),
        // Playwright version/browser differences may not expose these methods.
        // Guard them so the test won't crash and terminate early.
        fromDiskCache: typeof response.fromDiskCache === 'function' ? response.fromDiskCache() : null,
        fromServiceWorker: typeof response.fromServiceWorker === 'function' ? response.fromServiceWorker() : null,
        traceId:
          headers?.['x-trace-id'] ||
          headers?.['x-correlation-id'] ||
          headers?.['x-request-id'] ||
          null,
        durationMs,
        timestamp: new Date().toISOString(),
      })
    }
  })

  /**
   * End-of-test performance snapshot: Navigation / Resource Timing / paint (+ optional memory) in-page,
   * plus xhr/fetch round-trip stats from the same monitor. Call automatically from `saveTestMetadata` when `page` is passed.
   */
  async function snapshotPerformance(pg) {
    if (!performanceEnabled || !pg) {
      return performanceEnabled ? null : { skipped: true, reason: 'performance disabled via options.performance: false' }
    }
    const xhrFetchMonitored = summarizeMonitoredXhrFetch(events)
    let browser = null
    try {
      browser = await pg.evaluate(() => {
        const toMs = (n) => (typeof n === 'number' && n > 0 ? Math.round(n) : null)
        const nav = performance.getEntriesByType('navigation')[0]
        const paint = performance.getEntriesByType('paint').map((e) => ({
          name: e.name,
          startTimeMs: Math.round(e.startTime),
        }))
        let navigation = null
        if (nav) {
          const fs = nav.fetchStart || 0
          navigation = {
            type: nav.type,
            durationMs: toMs(nav.duration),
            domContentLoadedMs: toMs(nav.domContentLoadedEventEnd - fs),
            loadCompleteMs: toMs(nav.loadEventEnd - fs),
            domInteractiveMs: toMs(nav.domInteractive - fs),
          }
        }
        const resources = performance.getEntriesByType('resource')
        let resourceSummary = null
        if (resources.length) {
          const durs = resources.map((r) => r.duration).filter((d) => d > 0)
          const transferBytes = resources.reduce((s, r) => s + (r.transferSize || 0), 0)
          resourceSummary = {
            count: resources.length,
            transferSizeBytes: transferBytes,
            avgDurationMs: durs.length ? Math.round((durs.reduce((a, b) => a + b, 0) / durs.length) * 100) / 100 : null,
            maxDurationMs: durs.length ? Math.round(Math.max(...durs)) : null,
          }
        }
        const mem = typeof performance !== 'undefined' && performance.memory
          ? {
            usedJSHeapSize: performance.memory.usedJSHeapSize,
            totalJSHeapSize: performance.memory.totalJSHeapSize,
            jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
          }
          : null
        return { navigation, paint, resourceSummary, memory: mem }
      })
    } catch (err) {
      browser = { error: String(err?.message || err) }
    }
    return {
      timestamp: new Date().toISOString(),
      browser,
      xhrFetchMonitored,
    }
  }

  return {
    startTime,
    logs: events,
    performanceEnabled,
    snapshotPerformance,
  }
}

/** WeakMap so bound log objects are not enumerable on testInfo (Playwright metadata stays clean). */
const automationLogsByTestInfo = new WeakMap()

/**
 * Call once right after `setupConsoleLogging` / `setupNetworkMonitoring` / `setupEventListener`.
 * `flushAutomationLogsInAfterEach` then writes console/network/event JSON even when the test body
 * throws before an explicit `saveTestMetadata` (assertion failures, timeouts).
 */
function bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs) {
  if (!testInfo) return
  automationLogsByTestInfo.set(testInfo, { consoleLogs, networkLogs, eventLogs })
}

async function flushAutomationLogsInAfterEach(page, testInfo) {
  const payload = automationLogsByTestInfo.get(testInfo)
  if (!payload) return
  automationLogsByTestInfo.delete(testInfo)
  try {
    await saveTestMetadata(testInfo, payload.consoleLogs, payload.networkLogs, page, payload.eventLogs)
  } catch (err) {
    console.error('[util] flushAutomationLogsInAfterEach / saveTestMetadata failed:', err?.message || err)
  }
}

async function saveTestMetadata(testInfo, consoleLogs, networkLogs, page) {
  const outDir = testInfo.outputDir || path.join(process.cwd(), 'src/test/test-result')
  fs.mkdirSync(outDir, { recursive: true })

  const metadata = {
    testName: testInfo.title,
    status: testInfo.status,
    timestamp: new Date().toISOString(),
  }

  fs.writeFileSync(path.join(outDir, 'metadata.json'), JSON.stringify(metadata, null, 2))
  fs.writeFileSync(
    path.join(outDir, 'console-logs.json'),
    JSON.stringify(
      {
        startTime: consoleLogs?.startTime,
        endTime: new Date().toISOString(),
        logs: sortLogsByTimestamp(consoleLogs?.logs),
      },
      null,
      2
    )
  )
  let performanceSnapshot = null
  if (
    networkLogs &&
    typeof networkLogs.snapshotPerformance === 'function' &&
    networkLogs.performanceEnabled !== false &&
    page
  ) {
    try {
      performanceSnapshot = await networkLogs.snapshotPerformance(page)
    } catch (e) {
      performanceSnapshot = { error: String(e?.message || e) }
    }
  }

  fs.writeFileSync(
    path.join(outDir, 'network-logs.json'),
    JSON.stringify(
      {
        startTime: networkLogs?.startTime,
        endTime: new Date().toISOString(),
        logs: networkLogs?.logs || [],
        performance: performanceSnapshot,
      },
      null,
      2
    )
  )
  const eventLogs = arguments.length > 4 ? arguments[4] : null
  if (eventLogs && typeof eventLogs === 'object') {
    fs.writeFileSync(
      path.join(outDir, 'event-logs.json'),
      JSON.stringify(
        {
          startTime: eventLogs?.startTime,
          endTime: new Date().toISOString(),
          logs: eventLogs?.logs || [],
        },
        null,
        2
      )
    )
  }

  console.log(`🧾 Saved test metadata, console logs, and network logs → ${outDir}`)
}

// ============================================================
// EXPORTS
// ============================================================
export {
  setupEventListener,
  setupConsoleLogging,
  setupNetworkMonitoring,
  bindAutomationLogsForSave,
  flushAutomationLogsInAfterEach,
  saveTestMetadata,
  saveCurrentDom,
  injectSmoothCursor,
  moveCursorTo,
  highlightElement,
}
