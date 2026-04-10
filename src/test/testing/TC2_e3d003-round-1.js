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
  test('กดปุ่มสลับสถานะของงานเพื่อตรวจสอบการเปลี่ยนสถานะ', async ({ page }, testInfo) => {
    // Setup logging
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')
    await injectSmoothCursor(page)

    // --- T002: Observe page initial state and identify controls ---
    // Wait for main heading to be visible
    const elHeading = page.getByText('Your To Do', { exact: true })
    await expect(elHeading).toBeVisible({ timeout: 10000 })

    // --- T003: Complete the current required step (toggle status of one task) ---
    // Choose the first task toggle button (Design landing page)
    const elToggleButton = page.getByTestId('toggle-status-1')
    await expect(elToggleButton).toBeVisible({ timeout: 10000 })

    // Determine the task title locator for style checks
    const elTaskTitle = page.getByText('Design landing page', { exact: true })
    await expect(elTaskTitle).toBeVisible({ timeout: 10000 })

    // Check initial status by CSS class or style
    // Assuming that completed tasks have line-through style or a class like 'completed'
    // We will check before toggle, then toggle and re-check

    // Function to check if the task is marked completed (line-through or dimmed text)
    async function isTaskCompleted() {
      // Check computed style of text decoration or opacity
      const textDecoration = await elTaskTitle.evaluate(el => window.getComputedStyle(el).textDecoration)
      const opacity = await elTaskTitle.evaluate(el => window.getComputedStyle(el).opacity)
      // Return true if textDecoration includes 'line-through' or opacity is less than 1
      return textDecoration.includes('line-through') || parseFloat(opacity) < 1
    }

    // Get initial completion state
    const initialCompleted = await isTaskCompleted()

    // --- T004: Click toggle status button to switch state ---
    await moveCursorTo(page, elToggleButton, 1000)
    await highlightElement(page, elToggleButton)
    await elToggleButton.click()

    // Wait a short time for UI update
    await page.waitForTimeout(1000)

    // After toggle, check the changed state
    const afterCompleted = await isTaskCompleted()

    // The task status should be toggled
    expect(afterCompleted).not.toBe(initialCompleted)

    // The UI text should reflect new status (e.g., strikethrough appears or disappears)

    // End of test - final DOM snapshot and cleanup done by afterEach
  })
})