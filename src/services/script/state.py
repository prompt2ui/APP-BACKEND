from typing import TypedDict
MAX_ATTEMPTS = 3
MAX_TESTCASE_CONCURRENCY = 10


class AgentState(TypedDict, total=False):
    # --- set once by entrypoint, never changed by nodes ---
    test_url: str
    testcase: dict
    testcase_unique_id: str

    # --- shared memory across planner / evaluate ---
    testcase_script: str
    testcase_context: str
    testcase_suggestion: str
    testcase_result: str
    # testcase_context pattern (OVERWRITES each round, not accumulated):
    # ## TEST CASE GOAL
    # - testcase_name: <name>
    # - testcase_description: <description>
    #
    # ## MACHINE READABLE CONTEXT
    # ```json
    # {
    #   "goal": {...},
    #   "latest_evaluation": {...},
    #   "page_state": {...},
    #   "task_meta": {...},
    #   "task_board": [...]
    # }
    # ```
    #
    # ## TASK META
    # CURRENT_STEP: <current step label>
    # NEXT_TASK_IDS: T001,T002
    # PLANNER_MODE: patch_existing_script_only
    # COMPLETE_WHEN: <definition of done>
    #
    # ## TASK
    # [x] T001 | event=goto | ...
    # [ ] T002 | event=fill | ...
    #
    # ## LATEST EVALUATION (round N)
    # - Status: retry | complete
    # - Feedback Summary: <short summary>
    # - Root Cause: <why the script failed>
    # - Planner Violation: <present only when loop protection triggers>
    # - Page State: URL: ... | Heading: ...
    #
    # ### DO NOT USE SELECTORS
    # - <banned selector>
    #
    # ### USE INSTEAD
    # - <approved selector>
    #
    # ### FIX STEPS
    # - <imperative planner step>
    #
    # ## Available UI That Display After Test End
    # <full ui elements — no truncation>

    # --- execute output ---
    execute_attempt: int
    execute_result: str
    execute_log: str
    execute_screenshot: str
    execute_metadata_logs: dict
    execute_console_logs: dict
    execute_network_logs: dict
    execute_event_logs: dict
    execute_ui_elements: dict
    execute_current_url: str   # URL ของหน้าที่ test อยู่ตอน DOM snapshot (จาก ui-grouped-elements._url)
    execute_page_heading: str  # h1/h2/h3 ที่ visible — ใช้แยก SPA page state เมื่อ URL ไม่เปลี่ยน
