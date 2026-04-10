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
  test('ไม่สามารถเพิ่มงานใหม่เมื่อปล่อยช่องว่างว่างเปล่า', async ({ page }, testInfo) => {
    // Setup logs
    const consoleLogs = await setupConsoleLogging(page)
    const networkLogs = await setupNetworkMonitoring(page)
    const eventLogs = await setupEventListener(page)
    bindAutomationLogsForSave(testInfo, consoleLogs, networkLogs, eventLogs)

    // ---- T001: Open the target web page ----
    await page.goto('https://testing-todo-list.lovable.app/')
    await injectSmoothCursor(page)

    // ---- T002: Inspect initial visible UI ----
    // Confirm main heading 'Your To Do' is visible
    const el_heading = page.getByRole('heading', { name: 'Your To Do', exact: true })
    await expect(el_heading).toBeVisible({ timeout: 10000 })

    // Confirm the add task input is visible
    const el_taskInput = page.getByTestId('task-input')
    await expect(el_taskInput).toBeVisible({ timeout: 10000 })

    // Confirm the add task button is visible
    const el_addTaskButton = page.getByTestId('add-task-button')
    await expect(el_addTaskButton).toBeVisible({ timeout: 10000 })

    // ---- Before clearing input: capture and store initial number of tasks ----
    const taskToggleButtons = page.locator('button[data-testid^="toggle-status-"]')
    const initialTaskCount = await taskToggleButtons.count()

    // ---- T003: Complete current step (attempt to add empty task) ----
    // Clear input (empty string) to ensure blank
    await moveCursorTo(page, el_taskInput, 1000)
    await highlightElement(page, el_taskInput)
    await el_taskInput.fill('')

    // Wait shortly for frontend validation if any
    await page.waitForTimeout(1000)

    // ---- T004: Validate Add Task button disabled state ----
    const isEnabled = await el_addTaskButton.isEnabled()
    expect(isEnabled).toBe(false)

    // ---- T005: Validate task count unchanged and no empty task ----
    const currentTaskCount = await taskToggleButtons.count()
    expect(currentTaskCount).toBe(initialTaskCount)

    // Verify no tasks have empty text
    for (let i = 0; i < currentTaskCount; i++) {
      const taskText = await taskToggleButtons.nth(i).innerText()
      expect(taskText.trim().length).toBeGreaterThan(0)
    }

    // Test end
    await saveCurrentDom(page, testInfo)
  })
})