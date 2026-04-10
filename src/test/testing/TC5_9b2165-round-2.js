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
  test('ตรวจสอบว่าจำนวนงานที่เหลืออัปเดตถูกต้องเมื่องานเปลี่ยนสถานะหรือถูกลบ', async ({ page }, testInfo) => {
    // Setup logs
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')
    await injectSmoothCursor(page)

    // --- T002: Inspect the initial visible UI ---
    const elRemainingCount = page.getByText(/Your remaining todos : \d+/)
    await expect(elRemainingCount).toBeVisible()

    // --- T003 & T004: Interact with the UI and verify dynamic count updates ---

    // Helper function to parse current remaining todo count from visible text
    async function getRemainingCount() {
      const countText = await elRemainingCount.textContent()
      const match = countText.match(/Your remaining todos : (\d+)/)
      return match ? parseInt(match[1], 10) : null
    }

    // Read the initial remaining count (should be 4 initially as per UI)
    let initialCount = await getRemainingCount()
    expect(initialCount).toBeGreaterThan(0)

    // Step 1: Toggle status of 'Design landing page' task
    const elToggleStatusDesignLandingPage = page.getByRole('button', { name: 'Toggle status for Design landing page' })
    await expect(elToggleStatusDesignLandingPage).toBeVisible()

    await moveCursorTo(page, elToggleStatusDesignLandingPage, 1000)
    await highlightElement(page, elToggleStatusDesignLandingPage)
    await elToggleStatusDesignLandingPage.click()

    // Wait for UI update after toggle
    await page.waitForTimeout(1000)

    // Assert remaining todos count decreased by 1
    let currentCount = await getRemainingCount()
    expect(currentCount).toBe(initialCount - 1)

    // Step 2: Delete task 'Write API documentation'
    const elDeleteWriteAPI = page.getByRole('button', { name: 'Delete Write API documentation' })
    await expect(elDeleteWriteAPI).toBeVisible()

    await moveCursorTo(page, elDeleteWriteAPI, 1000)
    await highlightElement(page, elDeleteWriteAPI)
    await elDeleteWriteAPI.click()

    // Wait for UI update after delete
    await page.waitForTimeout(1000)

    // Assert remaining todos count decreased by 1 again
    let countAfterDelete = await getRemainingCount()
    expect(countAfterDelete).toBe(currentCount - 1)

    // Final save current DOM and artifacts
    await saveCurrentDom(page, testInfo)
  })
})