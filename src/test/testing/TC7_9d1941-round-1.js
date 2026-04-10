import { test, expect } from '@playwright/test'
import fs from 'fs'
import path from 'path'
import {
  setupEventListener,
  setupConsoleLogging,
  setupNetworkMonitoring,
  bindAutomationLogsForSave,
  flushAutomationLogsInAfterEach,
  saveCurrentDom,
  injectSmoothCursor,
  moveCursorTo,
  highlightElement,
} from '../util.js'

test.beforeEach(async ({}, testInfo) => {
  const baseResultDir = testInfo.project.outputDir
  const scriptName = path.basename(testInfo.file, path.extname(testInfo.file))
  const outDir = path.basename(baseResultDir) === scriptName
    ? baseResultDir
    : path.join(baseResultDir, scriptName)
  fs.mkdirSync(outDir, { recursive: true })
  testInfo.outputDir = outDir
})

test.afterEach(async ({ page }, testInfo) => {
  await flushAutomationLogsInAfterEach(page, testInfo)
  try {
    await saveCurrentDom(page, testInfo, { captureScreenshot: true })
  } catch (err) {
    console.error('[qa] saveCurrentDom failed:', err?.message || err)
  }
})

test.describe('Frontend Testing', () => {
  test('ตรวจสอบว่าเมื่อสลับขนาดหน้าจอเดสก์ท็อป แท็บเล็ต และมือถือ ยังเห็นเนื้อหาหลักของหน้าได้', async ({ page }, testInfo) => {
    // Setup logging
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')
    await injectSmoothCursor(page)

    // --- T002: Inspect the initial visible UI ---
    const elHeading = page.getByText('Your To Do').first()
    await expect(elHeading).toBeVisible({ timeout: 10000 })

    // No form steps to complete (this page is ToDo list main view, no multi-step form), so T003 and T004 are no-op.

    // --- Start Responsive Testing Rule: tablet → mobile → desktop ---
    // Use viewport sizes: tablet (834x1112), mobile (390x844), desktop (1280x720)

    // Tablet viewport
    await page.setViewportSize({ width: 834, height: 1112 })
    await page.waitForTimeout(1000)
    await saveCurrentDom(page, testInfo, { fileName: 'responsive-vp-tablet', captureScreenshot: true })

    // Check main content still visible on tablet
    await expect(elHeading).toBeVisible({ timeout: 10000 })

    // Mobile viewport
    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(1000)
    await saveCurrentDom(page, testInfo, { fileName: 'responsive-vp-mobile', captureScreenshot: true })

    // Check main content still visible on mobile
    await expect(elHeading).toBeVisible({ timeout: 10000 })

    // Desktop viewport
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.waitForTimeout(1000)
    await saveCurrentDom(page, testInfo, { fileName: 'responsive-vp-desktop', captureScreenshot: true })

    // Check main content still visible on desktop
    await expect(elHeading).toBeVisible({ timeout: 10000 })

  })
})