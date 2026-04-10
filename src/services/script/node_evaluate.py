import base64
import json
import os
import re
import shutil

from langchain.messages import HumanMessage, SystemMessage

from .state import AgentState, MAX_ATTEMPTS
from ..llm import llm_vision, llm, TESTING_DIR, RESULT_DIR
from ..prompt import Evaluator_SystemPrompt


def _strip_markdown_fences(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^```(?:json|javascript|js)?\s*\n?", "", s)
    s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _extract_goal(testcase_context: str) -> str:
    """Extract the immutable GOAL section from testcase_context (everything before first ## EVALUATION or ## LATEST)."""
    lines = testcase_context.split("\n")
    result = []
    for line in lines:
        if (
            line.strip().startswith("## MACHINE READABLE CONTEXT")
            or line.strip().startswith("## EVALUATION LOG")
            or line.strip().startswith("## LATEST EVALUATION")
        ):
            break
        result.append(line)
    # Trim trailing blank lines
    while result and not result[-1].strip():
        result.pop()
    return "\n".join(result)


def _short(text: str, limit: int = 180) -> str:
    value = (text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _extract_issue_from_log(execute_log: str) -> str:
    for line in (execute_log or "").splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(k in low for k in ["locator:", "error:", "timeout", "element(s) not found", "expect("]):
            return line
    return (execute_log or "").splitlines()[0].strip() if (execute_log or "").splitlines() else "Execution failed"


def _coerce_string_list(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [value]
    else:
        items = []

    result = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _normalize_feedback(feedback) -> dict:
    if isinstance(feedback, dict):
        normalized = {
            "summary": str(feedback.get("summary", "") or "").strip(),
            "root_cause": str(feedback.get("root_cause", "") or "").strip(),
            "do_not_use": _coerce_string_list(feedback.get("do_not_use")),
            "use_instead": _coerce_string_list(feedback.get("use_instead")),
            "steps": _coerce_string_list(feedback.get("steps")),
            "planner_violation": str(feedback.get("planner_violation", "") or "").strip(),
        }
    else:
        text = str(feedback or "").strip()
        normalized = {
            "summary": text,
            "root_cause": text,
            "do_not_use": [],
            "use_instead": [],
            "steps": [],
            "planner_violation": "",
        }

    if not normalized["root_cause"]:
        normalized["root_cause"] = normalized["summary"]
    if not normalized["summary"]:
        normalized["summary"] = normalized["root_cause"] or "No evaluator feedback provided."
    if not normalized["steps"] and normalized["use_instead"]:
        normalized["steps"] = ["Replace the banned selectors with the approved selectors from USE INSTEAD."]
    return normalized


def _feedback_blob(feedback: dict) -> str:
    parts = [
        feedback.get("summary", ""),
        feedback.get("root_cause", ""),
        feedback.get("planner_violation", ""),
        " ".join(feedback.get("do_not_use", [])),
        " ".join(feedback.get("use_instead", [])),
        " ".join(feedback.get("steps", [])),
    ]
    return " | ".join([part for part in parts if part]).strip()


def _extract_markdown_list_section(text: str, heading: str) -> list[str]:
    pattern = rf"{re.escape(heading)}\n(?P<body>(?:- .*(?:\n|$))+)"
    match = re.search(pattern, text or "")
    if not match:
        return []
    return [line[2:].strip() for line in match.group("body").splitlines() if line.startswith("- ")]


def _find_reused_banned_selectors(testcase_context: str, testcase_script: str) -> list[str]:
    if not testcase_context or not testcase_script:
        return []
    banned = _extract_markdown_list_section(testcase_context, "### DO NOT USE SELECTORS")
    return [selector for selector in banned if selector and selector in testcase_script]


def _default_task_meta() -> dict[str, str]:
    return {
        "CURRENT_STEP": "INITIAL_PAGE_LOAD",
        "NEXT_TASK_IDS": "T001,T002,T003,T004",
        "PLANNER_MODE": "patch_existing_script_only",
        "COMPLETE_WHEN": "all required tasks are marked [x] and the current UI is a true terminal/final state for the testcase goal",
    }


def _default_task_board(test_url: str) -> list[str]:
    return [
        f"[ ] T001 | event=goto | type=page | target=url | value={test_url} | required=true | note=Open the target web page",
        "[ ] T002 | event=observe | type=page | required=true | note=Inspect the initial visible UI and identify the current form step using grounded controls",
        "[ ] T003 | event=complete_current_step | type=form | required=true | note=Complete the current required step using visible labels, ids, roles, and validation messages from the live UI",
        "[ ] T004 | event=click_forward | type=button | required=true | note=Advance only after the current step is valid, then wait for the next step and continue from the updated UI",
    ]


def _extract_named_section(text: str, heading: str) -> str:
    pattern = rf"{re.escape(heading)}\n(?P<body>.*?)(?=\n## [A-Z]|\Z)"
    match = re.search(pattern, text or "", flags=re.DOTALL)
    return match.group("body").strip() if match else ""


def _parse_task_meta(text: str) -> dict[str, str]:
    meta = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        value = value.strip()
        if key:
            meta[key] = value
    return meta


def _format_task_meta(meta: dict[str, str]) -> str:
    ordered_keys = ("CURRENT_STEP", "NEXT_TASK_IDS", "PLANNER_MODE", "COMPLETE_WHEN")
    lines = []
    for key in ordered_keys:
        value = str(meta.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _task_line_id(line: str) -> str:
    match = re.match(r"^\[[xX ]\]\s+([A-Za-z0-9_-]+)", line.strip())
    return match.group(1) if match else ""


def _task_line_is_pending(line: str) -> bool:
    return line.strip().startswith("[ ]")


def _normalize_task_board(raw: str, fallback: list[str], test_url: str) -> list[str]:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    valid = [line for line in lines if re.match(r"^\[[xX ]\]\s+[A-Za-z0-9_-]+\b", line)]
    if valid:
        return valid
    if fallback:
        return fallback
    return _default_task_board(test_url)


def _derive_next_task_ids(task_board: list[str]) -> str:
    pending_ids = [_task_line_id(line) for line in task_board if _task_line_is_pending(line)]
    pending_ids = [task_id for task_id in pending_ids if task_id]
    return ",".join(pending_ids[:4]) if pending_ids else "NONE"


def _normalize_task_meta(raw: str, fallback: dict[str, str], task_board: list[str]) -> dict[str, str]:
    meta = dict(_default_task_meta())
    meta.update(fallback or {})
    meta.update(_parse_task_meta(raw))
    meta["PLANNER_MODE"] = "patch_existing_script_only"
    if not meta.get("COMPLETE_WHEN"):
        meta["COMPLETE_WHEN"] = _default_task_meta()["COMPLETE_WHEN"]
    if not meta.get("CURRENT_STEP"):
        meta["CURRENT_STEP"] = "UNKNOWN_CURRENT_STEP"
    meta["NEXT_TASK_IDS"] = _derive_next_task_ids(task_board)
    return meta


def _build_context_map(
    testcase: dict | None,
    attempt: int,
    eval_result: str,
    feedback: dict | None = None,
    testcase_suggestion: str = "",
    testcase_result: str = "",
    page_url: str = "",
    page_heading: str = "",
    page_state_label: str = "",
    task_meta: dict[str, str] | None = None,
    task_board: list[str] | None = None,
    note: str = "",
) -> dict:
    testcase = testcase or {}
    feedback = feedback or {}
    task_meta = task_meta or _default_task_meta()
    task_board = task_board or []
    return {
        "goal": {
            "testcase_name": testcase.get("testcase_name", ""),
            "testcase_description": testcase.get("testcase_description", ""),
            "testcase_category": testcase.get("testcase_category", ""),
            "testcase_priority": testcase.get("testcase_priority", ""),
        },
        "latest_evaluation": {
            "round": attempt,
            "status": eval_result,
            "summary": feedback.get("summary", ""),
            "root_cause": feedback.get("root_cause", ""),
            "do_not_use": feedback.get("do_not_use", []),
            "use_instead": feedback.get("use_instead", []),
            "steps": feedback.get("steps", []),
            "planner_violation": feedback.get("planner_violation", ""),
        },
        "page_state": {
            "url": page_url,
            "heading": page_heading,
            "label": page_state_label,
        },
        "task_meta": task_meta,
        "task_board": task_board,
        "testcase_suggestion": testcase_suggestion,
        "testcase_result": testcase_result,
        "note": note,
    }


def _build_context(
    goal_section: str,
    testcase: dict | None,
    attempt: int,
    eval_result: str,
    feedback: dict | None = None,
    testcase_suggestion: str = "",
    testcase_result: str = "",
    page_url: str = "",
    page_heading: str = "",
    page_state_label: str = "",
    task_meta: dict[str, str] | None = None,
    task_board: list[str] | None = None,
    ui_text: str = "",
    note: str = "",
) -> str:
    context_map = _build_context_map(
        testcase=testcase,
        attempt=attempt,
        eval_result=eval_result,
        feedback=feedback,
        testcase_suggestion=testcase_suggestion,
        testcase_result=testcase_result,
        page_url=page_url,
        page_heading=page_heading,
        page_state_label=page_state_label,
        task_meta=task_meta,
        task_board=task_board,
        note=note,
    )
    task_meta = task_meta or _default_task_meta()
    task_board = task_board or []

    parts = [
        goal_section.rstrip(),
        "",
        "## MACHINE READABLE CONTEXT",
        "```json",
        json.dumps(context_map, ensure_ascii=False, indent=2),
        "```",
        "",
        "## TASK META",
        _format_task_meta(task_meta),
        "",
        "## TASK",
        "\n".join(task_board),
        "",
        f"## LATEST EVALUATION (round {attempt})",
        f"- Status: {eval_result}",
    ]

    if feedback:
        if feedback.get("summary"):
            parts.append(f"- Feedback Summary: {feedback['summary']}")
            parts.append(f"- Feedback: {feedback['summary']}")
        if feedback.get("root_cause"):
            parts.append(f"- Root Cause: {feedback['root_cause']}")
        if feedback.get("planner_violation"):
            parts.append(f"- Planner Violation: {feedback['planner_violation']}")
    if testcase_suggestion:
        parts.append(f"- Suggestion: {testcase_suggestion}")
    if note:
        parts.append(f"- Note: {note}")
    if page_state_label:
        parts.append(f"- Page State: {page_state_label}")

    if feedback and feedback.get("do_not_use"):
        parts.extend(["", "### DO NOT USE SELECTORS"])
        parts.extend([f"- {item}" for item in feedback["do_not_use"]])
    if feedback and feedback.get("use_instead"):
        parts.extend(["", "### USE INSTEAD"])
        parts.extend([f"- {item}" for item in feedback["use_instead"]])
    if feedback and feedback.get("steps"):
        parts.extend(["", "### FIX STEPS"])
        parts.extend([f"- {item}" for item in feedback["steps"]])

    if ui_text:
        parts.extend(["", "## Available UI That Display After Test End", ui_text])

    return "\n".join([part for part in parts if part is not None]).rstrip() + "\n"


class NodeEvaluate:

    @staticmethod
    def _format_ui_elements(ui_elements: dict) -> str:
        if not ui_elements:
            return ""
        parts = []
        flat_categories = (
            "buttons",
            "clickable_cards",
            "inputs",
            "links",
            "dropdowns",
            "dialogs",
            "options",
        )
        is_flat = any(isinstance(ui_elements.get(k), list) for k in flat_categories)
        if is_flat:
            for category in flat_categories:
                items = ui_elements.get(category, [])
                if not items:
                    continue
                lines = [f"### {category} ({len(items)})"]
                for item in items[:15]:
                    label = item.get("label", "")[:60]
                    role = item.get("role", "")
                    tag = item.get("tag", "")
                    lines.append(f"  - [{role or tag}] {label}")
                parts.append("\n".join(lines))
        else:
            for container_key, container in list(ui_elements.items())[:8]:
                if not isinstance(container, dict):
                    continue
                header = container.get("label") or container_key
                lines = [f"### container: {header}"]
                for category in (
                    "buttons",
                    "clickable_cards",
                    "inputs",
                    "links",
                    "dropdowns",
                    "options",
                ):
                    items = container.get(category, [])
                    if not isinstance(items, list) or not items:
                        continue
                    lines.append(f"- {category}:")
                    for item in items[:8]:
                        label = item.get("label", "")[:60]
                        role = item.get("role", "")
                        tag = item.get("tag", "")
                        kv = [f'label="{label}"']
                        item_text = item.get("text", "")
                        if item_text and item_text != label:
                            kv.append(f'text="{item_text}"')
                        if item.get("id"):
                            kv.append(f'id="{item["id"]}"')
                        if item.get("name") and item["name"] != label:
                            kv.append(f'name="{item["name"]}"')
                        if item.get("placeholder"):
                            kv.append(f'placeholder="{item["placeholder"]}"')
                        if item.get("data_testid"):
                            kv.append(f'data-testid="{item["data_testid"]}"')
                        # sr-only flag for custom radio/checkbox
                        if item.get("sr_only"):
                            kv.append("sr-only=true")
                        suffix = " | " + ", ".join(kv)
                        lines.append(f"  - [{role or tag}] {label}{suffix}")
                        # Interaction hint for sr-only elements
                        if item.get("interaction_hint"):
                            lines.append(f"      hint: {item['interaction_hint']}")
                        # Inline select options
                        if item.get("select_options"):
                            opts_str = ", ".join([f'"{o.get("value", "")}"' for o in item["select_options"]])
                            lines.append(f"    → Available options: [{opts_str}] — use selectOption(value)")
                if len(lines) > 1:
                    parts.append("\n".join(lines))
            root = ui_elements.get("root", {}) if isinstance(ui_elements, dict) else {}
            texts = root.get("texts", []) if isinstance(root, dict) else []
            if isinstance(texts, list) and texts:
                parts.append("### visible texts\n" + "\n".join([f"- {str(t)[:160]}" for t in texts[:20]]))
        return "\n".join(parts)

    @staticmethod
    def _format_event_logs(event_logs: dict) -> str:
        if not isinstance(event_logs, dict):
            return ""
        logs = event_logs.get("logs", [])
        if not isinstance(logs, list) or not logs:
            return ""
        lines = ["### recent events"]
        for event in logs[-20:]:
            if not isinstance(event, dict):
                continue
            et = event.get("type", "")
            label = event.get("label", "")
            url = event.get("url", "")
            method = event.get("method", "")
            parts = [str(x) for x in [et, method, label, url] if x]
            if parts:
                lines.append(f"- {' | '.join(parts)[:180]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _build_run_id(testcase_unique_id: str, attempt: int) -> str:
        return f"{testcase_unique_id}-round-{attempt}"

    @staticmethod
    def _cleanup_round(testcase_unique_id: str, attempt: int):
        """Delete script + result folder for a given round."""
        if attempt < 1:
            return
        run_id = NodeEvaluate._build_run_id(testcase_unique_id, attempt)
        prev_script = os.path.join(TESTING_DIR, f"{run_id}.js")
        prev_result = os.path.join(RESULT_DIR, run_id)
        try:
            if os.path.exists(prev_script):
                os.remove(prev_script)
        except OSError:
            pass
        try:
            if os.path.isdir(prev_result):
                shutil.rmtree(prev_result)
        except OSError:
            pass

    @staticmethod
    async def run(state: AgentState) -> AgentState:
        testcase_context = state.get("testcase_context", "")
        testcase = state.get("testcase", {})
        test_url = state.get("test_url", "")
        testcase_script = state.get("testcase_script", "")
        testcase_unique_id = state.get("testcase_unique_id", "unnamed")
        execute_result = state.get("execute_result", "fail")
        execute_log = state.get("execute_log", "")
        execute_screenshot = state.get("execute_screenshot", "")
        execute_event_logs = state.get("execute_event_logs", {})
        execute_ui_elements = state.get("execute_ui_elements", {})
        execute_current_url = state.get("execute_current_url", "")
        execute_page_heading = state.get("execute_page_heading", "")
        attempt = state.get("execute_attempt", 1)

        # Build page state label
        _page_state_parts = []
        if execute_current_url:
            _page_state_parts.append(f"URL: {execute_current_url}")
        if execute_page_heading:
            _page_state_parts.append(f"Heading: {execute_page_heading}")
        page_state_label = " | ".join(_page_state_parts)

        # Format reusable text blocks
        ui_text = NodeEvaluate._format_ui_elements(execute_ui_elements)
        event_text = NodeEvaluate._format_event_logs(execute_event_logs)

        # Extract immutable goal section from testcase_context
        goal_section = _extract_goal(testcase_context)
        previous_task_board = _normalize_task_board(
            _extract_named_section(testcase_context, "## TASK"),
            fallback=[],
            test_url=test_url,
        )
        previous_task_meta = _normalize_task_meta(
            _extract_named_section(testcase_context, "## TASK META"),
            fallback=_default_task_meta(),
            task_board=previous_task_board,
        )

        # ------------------------------------------------------------------
        # Evaluate latest run — pass/fail both go through evaluator LLM
        # ------------------------------------------------------------------
        reused_banned_selectors = _find_reused_banned_selectors(testcase_context, testcase_script)
        if attempt > 1 and reused_banned_selectors:
            prior_replacements = _extract_markdown_list_section(testcase_context, "### USE INSTEAD")
            loop_feedback = _normalize_feedback({
                "summary": "Planner reused selectors that were explicitly banned in the previous evaluation.",
                "root_cause": (
                    "The latest script still contains selectors from DO NOT USE SELECTORS, so the planner "
                    "did not apply the retry constraints from the previous round."
                ),
                "do_not_use": reused_banned_selectors,
                "use_instead": prior_replacements,
                "steps": [
                    "Stop retrying this testcase because the planner is repeating a banned selector.",
                    "Review why the planner ignored DO NOT USE SELECTORS before running another round.",
                ],
                "planner_violation": (
                    "Loop protection triggered: the planner reused a selector that was already banned in the prior round."
                ),
            })
            loop_context = _build_context(
                goal_section=goal_section,
                testcase=testcase,
                attempt=attempt,
                eval_result="complete",
                feedback=loop_feedback,
                testcase_result="fail",
                page_url=execute_current_url,
                page_heading=execute_page_heading,
                page_state_label=page_state_label,
                task_meta=previous_task_meta,
                task_board=previous_task_board,
                ui_text=ui_text,
            )

            issue = _extract_issue_from_log(execute_log)
            print("[EVALUATE]")
            print("* Result : complete")
            print("* Issue  :")
            print(issue)
            print("* Cause  :")
            print(loop_feedback["root_cause"])
            print("* Fix    :")
            print(loop_feedback["planner_violation"])
            print("")

            NodeEvaluate._cleanup_round(testcase_unique_id, attempt - 1)
            return {
                "execute_result": "complete",
                "testcase_context": loop_context,
                "testcase_result": "fail",
                "testcase_suggestion": "",
            }

        user_parts = [
            f"## Testcase Context\n{testcase_context}\n",
            f"## Playwright Run Status\n- Result: {execute_result}\n",
        ]

        if testcase_script:
            user_parts.append(f"## Current Test Script\n```javascript\n{testcase_script}\n```\n")

        if execute_log:
            user_parts.append(f"## Execution Log\n{execute_log}\n")

        if page_state_label:
            user_parts.append(
                f"## Current Page State (where the script stopped)\n"
                f"- {page_state_label}\n"
                f"- The UI elements below were captured at this exact point.\n"
                f"- The next script MUST reproduce all steps to reach this state, then continue from here.\n"
            )
        if ui_text:
            user_parts.append(
                f"## Available UI That Display After Test End (captured from the live page after last run)\n"
                f"{ui_text}\n"
            )
        if event_text:
            user_parts.append(f"## Event Timeline\n{event_text}\n")

        user_parts.append(f"## Round {attempt} of {MAX_ATTEMPTS}")

        user_text = "\n".join(user_parts)

        # Build LLM messages — always try to attach screenshot
        messages = [SystemMessage(content=Evaluator_SystemPrompt)]

        use_vision = False
        img_b64 = ""
        if execute_screenshot and execute_screenshot.strip():
            try:
                with open(execute_screenshot, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
                use_vision = True
            except Exception:
                use_vision = False

        if use_vision and img_b64:
            messages.append(HumanMessage(content=[
                {"type": "text", "text": user_text + "\n\n## Screenshot\nSee attached image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]))
        else:
            messages.append(HumanMessage(content=user_text))

        model = llm_vision if use_vision else llm

        response = await model.ainvoke(messages)

        raw = _strip_markdown_fences(response.content.strip())

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {
                "result": "retry",
                "goal_status": "",
                "task_meta": _format_task_meta(previous_task_meta),
                "task_board": "\n".join(previous_task_board),
                "feedback": {
                    "summary": "Evaluator returned invalid JSON.",
                    "root_cause": f"Evaluator returned invalid JSON: {raw[:300]}",
                    "do_not_use": [],
                    "use_instead": [],
                    "steps": ["Return valid JSON that matches the evaluator schema."],
                    "planner_violation": "",
                },
                "testcase_suggestion": "",
            }

        eval_result = parsed.get("result", "retry")
        goal_status = str(parsed.get("goal_status", "") or "").strip().lower()
        feedback = _normalize_feedback(parsed.get("feedback", ""))
        testcase_suggestion = parsed.get("testcase_suggestion", "") or ""
        task_board = _normalize_task_board(
            parsed.get("task_board", ""),
            fallback=previous_task_board,
            test_url=test_url,
        )
        task_meta = _normalize_task_meta(
            parsed.get("task_meta", ""),
            fallback=previous_task_meta,
            task_board=task_board,
        )

        # Guard: selector/timing issues should be retryable
        if eval_result == "complete" and attempt < MAX_ATTEMPTS:
            lower_log = (execute_log or "").lower()
            selector_issue = any(
                s in lower_log
                for s in [
                    "element(s) not found",
                    "tobevisible",
                    "waiting for",
                    "locator:",
                    "timeout",
                    "timed out",
                    "strict mode violation",
                    "page.waitfortimeout: test ended",
                ]
            )
            if selector_issue:
                eval_result = "retry"
                if not feedback.get("summary"):
                    feedback["summary"] = "The script failed to find or see the expected UI elements."
                if not feedback.get("root_cause"):
                    feedback["root_cause"] = "The failure looks like a selector or timing issue."
                if not feedback.get("steps"):
                    feedback["steps"] = [
                        "Update the locator or timing to match the available UI elements from the scanned page state."
                    ]
                testcase_suggestion = (
                    testcase_suggestion
                    or "Retry by updating the locator/role to match the 'Available UI That Display After Test End' from the scanned page state."
                )
                goal_status = ""

        # Guardrail: assertion mismatch
        lower_log = execute_log.lower()
        lower_feedback = _feedback_blob(feedback).lower()
        assertion_like = any(k in lower_log for k in ["to be", "expected", "assert", "expect("])
        logic_mismatch = any(
            k in lower_feedback
            for k in ["assumption", "expected 2 but got", "count changes differently", "test logic", "assertion"]
        )
        if eval_result == "complete" and attempt < MAX_ATTEMPTS and assertion_like and logic_mismatch:
            eval_result = "retry"
            if not testcase_suggestion:
                testcase_suggestion = (
                    "Improve counter UX by showing explicit state change feedback (e.g., toast or inline status)"
                )
            feedback["summary"] = feedback.get("summary") or "The assertion logic is fixable in the script."
            feedback["root_cause"] = (
                (feedback.get("root_cause") or feedback.get("summary") or "").strip()
                + " This looks fixable in script: infer direction dynamically (count can increase or decrease), "
                  "or pick a known incomplete task before asserting delta."
            ).strip()
            if not feedback.get("steps"):
                feedback["steps"] = [
                    "Adjust the assertion to infer direction dynamically instead of hardcoding the counter delta."
                ]
            goal_status = ""

        has_pending_tasks = any(_task_line_is_pending(line) for line in task_board)
        if eval_result == "complete" and attempt < MAX_ATTEMPTS and has_pending_tasks:
            eval_result = "retry"
            goal_status = ""
            feedback["summary"] = feedback.get("summary") or "The task board still has required pending work."
            feedback["root_cause"] = (
                "The latest evaluation tried to complete the testcase even though the task board still has pending tasks. "
                "The planner must continue with NEXT_TASK_IDS until all required tasks are marked [x]."
            )
            if not feedback.get("steps"):
                feedback["steps"] = [
                    "Continue implementing the pending tasks listed in NEXT_TASK_IDS.",
                ]

        testcase_result = ""
        if eval_result == "complete":
            if goal_status in {"pass", "fail"}:
                testcase_result = goal_status
            else:
                testcase_result = "pass" if execute_result == "pass" else "fail"

        issue = _extract_issue_from_log(execute_log)
        cause = feedback.get("root_cause") or feedback.get("summary", "")
        fix = testcase_suggestion or " | ".join(feedback.get("use_instead", []) or feedback.get("steps", []))
        print("[EVALUATE]")
        print(f"* Result : {eval_result}")
        print("* Issue  :")
        print(issue)
        print("* Cause  :")
        print(cause)
        print("* Fix    :")
        print(fix)
        print("")

        # ------------------------------------------------------------------
        # Build new testcase_context — OVERWRITE, not append
        # goal_section stays, then LATEST EVALUATION replaces all previous
        # ------------------------------------------------------------------
        new_context = _build_context(
            goal_section=goal_section,
            testcase=testcase,
            attempt=attempt,
            eval_result=eval_result,
            feedback=feedback,
            testcase_suggestion=testcase_suggestion,
            testcase_result=testcase_result,
            page_url=execute_current_url,
            page_heading=execute_page_heading,
            page_state_label=page_state_label,
            task_meta=task_meta,
            task_board=task_board,
            ui_text=ui_text,
        )

        # ------------------------------------------------------------------
        # Complete — stop retrying
        # ------------------------------------------------------------------
        if eval_result == "complete":
            # Cleanup previous round
            NodeEvaluate._cleanup_round(testcase_unique_id, attempt - 1)
            return {
                "execute_result": "complete",
                "testcase_context": new_context,
                "testcase_result": testcase_result,
                "testcase_suggestion": testcase_suggestion,
            }

        if attempt >= MAX_ATTEMPTS:
            new_context = _build_context(
                goal_section=goal_section,
                testcase=testcase,
                attempt=attempt,
                eval_result="complete",
                feedback=feedback,
                testcase_suggestion=testcase_suggestion,
                testcase_result="fail",
                page_url=execute_current_url,
                page_heading=execute_page_heading,
                page_state_label=page_state_label,
                task_meta=task_meta,
                task_board=task_board,
                ui_text=ui_text,
                note=f"Max attempts ({MAX_ATTEMPTS}) reached, stopping.",
            )
            # Cleanup previous round
            NodeEvaluate._cleanup_round(testcase_unique_id, attempt - 1)
            return {
                "execute_result": "complete",
                "testcase_context": new_context,
                "testcase_result": "fail",
                "testcase_suggestion": testcase_suggestion,
            }

        # ------------------------------------------------------------------
        # Retry — cleanup previous round, return updated context
        # ------------------------------------------------------------------
        NodeEvaluate._cleanup_round(testcase_unique_id, attempt - 1)

        return {
            "execute_result": "retry",
            "testcase_context": new_context,
            "testcase_result": "",
            "testcase_suggestion": testcase_suggestion,
        }
