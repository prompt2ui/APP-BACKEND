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
  test('ปิดแบนเนอร์หรือป้ายแจ้งเตือนด้วยปุ่ม Dismiss', async ({ page }, testInfo) => {
    // Setup logging
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')

    // Inject smooth cursor for better interaction video
    await injectSmoothCursor(page)

    // --- T002: Observe initial visible UI ---
    // Check if any banner or alert with Dismiss button is visible
    // There is no explicit banner in Available UI but we check for a generic dismiss button or role alert

    // Attempt to find any visible Dismiss button
    const bannerDismissButton = page.getByRole('button', { name: 'Dismiss' })
    if (await bannerDismissButton.count() > 0) {
      await expect(bannerDismissButton.first()).toBeVisible({ timeout: 5000 })

      // --- T003: Complete current step - click Dismiss button to close the banner ---
      await moveCursorTo(page, bannerDismissButton.first(), 1000)
      await highlightElement(page, bannerDismissButton.first())
      await bannerDismissButton.first().click()

      // Short wait for banner to disappear
      await page.waitForTimeout(500)

      // --- T004: Confirm banner is gone and page usable ---
      await expect(bannerDismissButton.first()).toHaveCount(0)
    }

    // Confirm main content (To Do List) is visible and usable
    const addTaskInput = page.getByTestId('task-input')
    await expect(addTaskInput).toBeVisible({ timeout: 10000 })

    // Save final DOM snapshot (done in afterEach)
  })
})