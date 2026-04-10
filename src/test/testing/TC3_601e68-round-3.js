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
  test('ลบงานจากรายการและตรวจสอบว่าไม่แสดงแล้ว', async ({ page }, testInfo) => {
    // Setup logging and monitoring
    const eventLogs = await setupEventListener(page)
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')

    // Inject smooth cursor for reliable interaction
    await injectSmoothCursor(page)

    // --- T002: Inspect initial visible UI ---
    // Get locator for remaining todos count text
    const elRemainingCount = page.getByText(/Your remaining todos : \d+/)
    await expect(elRemainingCount).toBeVisible({ timeout: 10000 })

    // Store initial count text before deletion
    const initialCountText = await elRemainingCount.innerText()

    // --- T003: Complete current step (delete one task) ---
    // Use the recommended testId selector for the delete button of the visible task 'Write API documentation'
    const elTaskToDelete = page.getByTestId('delete-task-2')
    await expect(elTaskToDelete).toBeVisible({ timeout: 10000 })
    await moveCursorTo(page, elTaskToDelete, 1000)
    await highlightElement(page, elTaskToDelete)
    await elTaskToDelete.click()

    // Wait for the task text to disappear indicating successful deletion
    const elDeletedTaskText = page.getByText('Write API documentation')
    await expect(elDeletedTaskText).toHaveCount(0, { timeout: 10000 })

    // --- T004: Verify UI update for remaining todos count ---
    // Wait for a short moment to let UI settle
    await page.waitForTimeout(1000)

    // Re-acquire the remaining todos count text locator
    const elRemainingCountUpdated = page.getByText(/Your remaining todos : \d+/)
    await expect(elRemainingCountUpdated).toBeVisible({ timeout: 10000 })
    const updatedCountText = await elRemainingCountUpdated.innerText()

    // Extract counts as numbers
    const initialMatch = initialCountText.match(/Your remaining todos : (\d+)/)
    const updatedMatch = updatedCountText.match(/Your remaining todos : (\d+)/)
    const initialCount = initialMatch ? parseInt(initialMatch[1], 10) : null
    const updatedCount = updatedMatch ? parseInt(updatedMatch[1], 10) : null

    // Assert updated count is less than initial by at least 1 (flexible)
    if (initialCount !== null && updatedCount !== null) {
      expect(updatedCount).toBeLessThanOrEqual(initialCount - 1)
    }
  })
})