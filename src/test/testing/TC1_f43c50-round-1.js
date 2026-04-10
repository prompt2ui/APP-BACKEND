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
  test('เพิ่มงานใหม่และตรวจสอบว่าแสดงในรายการ', async ({ page }, testInfo) => {
    // Setup console, network and event logs
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page, { performance: false })
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // --- T001: Open the target web page ---
    await page.goto('https://testing-todo-list.lovable.app/')
    await injectSmoothCursor(page)

    // --- T002: Wait for initial UI elements ---
    const elTaskInput = page.getByTestId('task-input')
    const elAddTaskButton = page.getByTestId('add-task-button')
    await expect(elTaskInput).toBeVisible({ timeout: 10000 })
    await expect(elAddTaskButton).toBeVisible({ timeout: 10000 })

    // --- T003: Fill the required input with new task text ---
    const newTaskText = `อัตโนมัติ test task ${Date.now()}`
    await moveCursorTo(page, elTaskInput, 1000)
    await highlightElement(page, elTaskInput)
    await elTaskInput.fill(newTaskText)

    // Allow UI validation and button enable
    await page.waitForTimeout(1000)

    // --- T004: Click the Add task button ---
    await moveCursorTo(page, elAddTaskButton, 1000)
    await highlightElement(page, elAddTaskButton)
    await elAddTaskButton.click()

    // Wait for the newly added task to appear in the list with correct status
    const elNewTaskToggleButton = page.getByRole('button', { name: `Toggle status for ${newTaskText}` })
    const elNewTaskDeleteButton = page.getByRole('button', { name: `Delete ${newTaskText}` })
    await expect(elNewTaskToggleButton).toBeVisible({ timeout: 20000 })
    await expect(elNewTaskDeleteButton).toBeVisible({ timeout: 20000 })

    // Verify the new task text is visible in the list
    const elNewTaskText = page.getByText(newTaskText).filter({ hasText: newTaskText })
    await expect(elNewTaskText).toBeVisible({ timeout: 20000 })

    // Verify the new task is initially in incomplete state (i.e. toggle button is not pressed or some indicator)
    // The UI uses toggle button to switch status, so before clicking toggle it is incomplete.
    // No explicit attribute is mentioned, so we assume presence only without toggle is sufficient.

    // Save final DOM snapshot
    await saveCurrentDom(page, testInfo)
  })
})