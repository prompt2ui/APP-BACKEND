import base64
import json
import re

from langchain.messages import HumanMessage, SystemMessage

from .state import AgentState
from ..llm import llm, llm_vision
from ..prompt import Planner_SystemPrompt


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences that the LLM might wrap around JSON."""
    s = text.strip()
    s = re.sub(r"^```(?:json|javascript|js)?\s*\n?", "", s)
    s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _short(text: str, limit: int = 180) -> str:
    value = (text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _extract_markdown_list_section(text: str, heading: str) -> list[str]:
    pattern = rf"{re.escape(heading)}\n(?P<body>(?:- .*(?:\n|$))+)"
    match = re.search(pattern, text or "")
    if not match:
        return []
    return [line[2:].strip() for line in match.group("body").splitlines() if line.startswith("- ")]


def _extract_named_section(text: str, heading: str) -> str:
    pattern = rf"{re.escape(heading)}\n(?P<body>.*?)(?=\n## [A-Z]|\Z)"
    match = re.search(pattern, text or "", flags=re.DOTALL)
    return match.group("body").strip() if match else ""


def _extract_context_map(testcase_context: str) -> dict:
    matches = re.findall(
        r"## MACHINE READABLE CONTEXT\s*```json\s*(\{.*?\})\s*```",
        testcase_context or "",
        flags=re.DOTALL,
    )
    if not matches:
        return {}

    raw = matches[-1].strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_latest_field(testcase_context: str, field_name: str) -> str:
    matches = re.findall(rf"- {re.escape(field_name)}:\s*(.+)", testcase_context or "")
    return matches[-1].strip() if matches else ""


def _parse_task_meta(task_meta_text: str) -> dict[str, str]:
    meta = {}
    for line in (task_meta_text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        value = value.strip()
        if key:
            meta[key] = value
    return meta


def _parse_task_line(line: str) -> dict:
    line = (line or "").strip()
    match = re.match(r"^\[(?P<status>[xX ])\]\s+(?P<task_id>[A-Za-z0-9_-]+)(?:\s+\|\s*(?P<body>.*))?$", line)
    if not match:
        return {}

    task = {
        "status": "done" if match.group("status").lower() == "x" else "pending",
        "task_id": match.group("task_id"),
        "raw": line,
    }
    body = match.group("body") or ""
    for segment in [part.strip() for part in body.split("|") if part.strip()]:
        if "=" in segment:
            key, value = segment.split("=", 1)
            task[key.strip().lower()] = value.strip()
        else:
            task.setdefault("note", segment)
    return task


def _extract_task_contract(testcase_context: str) -> dict:
    task_meta = _parse_task_meta(_extract_named_section(testcase_context, "## TASK META"))
    task_lines = [
        line.strip()
        for line in _extract_named_section(testcase_context, "## TASK").splitlines()
        if line.strip().startswith("[")
    ]
    tasks = [task for task in (_parse_task_line(line) for line in task_lines) if task]

    raw_next_ids = str(task_meta.get("NEXT_TASK_IDS", "") or "").strip()
    next_task_ids = []
    if raw_next_ids and raw_next_ids.upper() != "NONE":
        next_task_ids = [item.strip() for item in raw_next_ids.split(",") if item.strip()]

    if next_task_ids:
        next_tasks = [task for task in tasks if task.get("task_id") in next_task_ids]
    else:
        next_tasks = [task for task in tasks if task.get("status") == "pending"][:4]
        next_task_ids = [task.get("task_id", "") for task in next_tasks if task.get("task_id")]

    return {
        "meta": task_meta,
        "tasks": tasks,
        "next_task_ids": next_task_ids,
        "next_tasks": next_tasks,
    }


def _extract_feedback_constraints(testcase_context: str) -> dict:
    context_map = _extract_context_map(testcase_context)
    latest_evaluation = context_map.get("latest_evaluation", {}) if isinstance(context_map, dict) else {}

    return {
        "summary": str(latest_evaluation.get("summary", "") or "").strip()
        or _extract_latest_field(testcase_context, "Feedback Summary")
        or _extract_latest_field(testcase_context, "Feedback"),
        "root_cause": str(latest_evaluation.get("root_cause", "") or "").strip()
        or _extract_latest_field(testcase_context, "Root Cause"),
        "do_not_use": [
            str(item).strip()
            for item in (latest_evaluation.get("do_not_use", []) if isinstance(latest_evaluation.get("do_not_use", []), list) else [])
            if str(item).strip()
        ] or _extract_markdown_list_section(testcase_context, "### DO NOT USE SELECTORS"),
        "use_instead": [
            str(item).strip()
            for item in (latest_evaluation.get("use_instead", []) if isinstance(latest_evaluation.get("use_instead", []), list) else [])
            if str(item).strip()
        ] or _extract_markdown_list_section(testcase_context, "### USE INSTEAD"),
        "steps": [
            str(item).strip()
            for item in (latest_evaluation.get("steps", []) if isinstance(latest_evaluation.get("steps", []), list) else [])
            if str(item).strip()
        ] or _extract_markdown_list_section(testcase_context, "### FIX STEPS"),
    }


def _extract_latest_fix_hint(testcase_context: str) -> str:
    constraints = _extract_feedback_constraints(testcase_context)
    task_contract = _extract_task_contract(testcase_context)
    parts = [
        f"NEXT_TASK_IDS={','.join(task_contract['next_task_ids'])}" if task_contract["next_task_ids"] else "",
        constraints["summary"],
        constraints["root_cause"],
        constraints["use_instead"][0] if constraints["use_instead"] else "",
        constraints["steps"][0] if constraints["steps"] else "",
    ]
    return " | ".join([part for part in parts if part]).strip()


def _parse_planner_response(raw_text: str) -> tuple[str, str]:
    raw = _strip_markdown_fences(raw_text.strip())
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[Planner] JSON Parse Error. Raw response was: {raw[:500]}...")
        parsed = {"test_script": raw, "intent": ""}

    test_script = parsed.get("test_script", "")
    intent = parsed.get("intent", "")

    if test_script:
        test_script = _strip_markdown_fences(test_script)

    return test_script, intent


def _find_banned_selector_hits(test_script: str, banned_selectors: list[str]) -> list[str]:
    if not test_script or not banned_selectors:
        return []

    hits = []
    for selector in banned_selectors:
        variants = {selector}
        if "'" in selector:
            variants.add(selector.replace("'", '"'))
        if '"' in selector:
            variants.add(selector.replace('"', "'"))
        if any(variant and variant in test_script for variant in variants):
            hits.append(selector)
    return hits


def _find_invalid_shortcut_hits(test_script: str) -> list[str]:
    patterns = [
        "page.locator('input').first()",
        'page.locator("input").first()',
        "page.locator('input, textarea').all()",
        'page.locator("input, textarea").all()',
        "page.getByRole('button', { name: /next|continue|submit/i }).first()",
        'page.getByRole("button", { name: /next|continue|submit/i }).first()',
        "expect(page.locator('body')).toBeVisible()",
        'expect(page.locator("body")).toBeVisible()',
        "page.getByRole('heading')",
        'page.getByRole("heading")',
        "page.getByText(/thank you|success|complete|finished/i)",
        'page.getByText(/step\\s*3|step\\s*4|final/i)',
    ]
    return [pattern for pattern in patterns if pattern in (test_script or "")]


def _format_next_tasks(next_tasks: list[dict]) -> str:
    lines = []
    for task in next_tasks:
        parts = [task.get("task_id", "")]
        for key in ("event", "type", "id", "label", "role", "value", "locator_hint", "expect", "note"):
            value = str(task.get(key, "") or "").strip()
            if value:
                parts.append(f"{key}={value}")
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


class NodePlanner:

    @staticmethod
    async def run(state: AgentState) -> AgentState:
        testcase_context = state.get("testcase_context", "")
        test_url = state.get("test_url", "")
        execute_screenshot = state.get("execute_screenshot", "")
        testcase_script = state.get("testcase_script", "")
        round_number = int(state.get("execute_attempt", 0) or 0) + 1
        constraints = _extract_feedback_constraints(testcase_context)
        task_contract = _extract_task_contract(testcase_context)

        user_parts = [
            f"## Target URL\n{test_url}\n",
            f"## Testcase Context\n{testcase_context}\n",
        ]

        if task_contract["next_tasks"]:
            user_parts.append(
                "## Immediate Tasks To Implement\n"
                f"- NEXT_TASK_IDS: {','.join(task_contract['next_task_ids'])}\n"
                f"- CURRENT_STEP: {task_contract['meta'].get('CURRENT_STEP', '')}\n"
                "- Only patch the script to perform these pending tasks in order.\n"
                f"{_format_next_tasks(task_contract['next_tasks'])}\n"
            )

        # On retry rounds, include the previous script so LLM can fix/extend it
        if testcase_script and testcase_script.strip():
            user_parts.append(
                f"## Previous Test Script (this script produced the current DOM/screenshot below — FIX or EXTEND it, do NOT rewrite from scratch)\n"
                f"```javascript\n{testcase_script}\n```\n"
            )

        use_vision = False
        img_b64 = ""
        if execute_screenshot and execute_screenshot.strip():
            try:
                with open(execute_screenshot, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
                use_vision = True
            except Exception:
                use_vision = False

        user_text = "\n".join(user_parts)

        messages = [SystemMessage(content=Planner_SystemPrompt)]

        if use_vision and img_b64:
            messages.append(HumanMessage(content=[
                {"type": "text", "text": user_text + "\n\n## Screenshot (current page state)\nSee the attached image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]))
        else:
            messages.append(HumanMessage(content=user_text))

        model = llm_vision if use_vision else llm
        # ROUND HEADER + compact planner logs (strict, scannable).
        print(f"\n\n\n==================== ROUND {round_number} ====================\n\n\n")
        latest_fix_hint = _extract_latest_fix_hint(testcase_context)
        goal = f"Generate/fix script for {state.get('testcase', {}).get('testcase_name', 'current testcase')}"
        change = "Initial draft from testcase context" if round_number == 1 else "Apply latest evaluation feedback"
        focus = latest_fix_hint or ",".join(task_contract["next_task_ids"]) or "Critical selector/step that failed in previous run"
        print("[PLANNER]")
        print(f"* Goal   : {goal}")
        print(f"* Change : {change}")
        print(f"* Focus  : {focus}")

        response = await model.ainvoke(messages)
        test_script, intent = _parse_planner_response(response.content)

        banned_hits = _find_banned_selector_hits(test_script, constraints["do_not_use"])
        shortcut_hits = _find_invalid_shortcut_hits(test_script)
        if banned_hits or shortcut_hits:
            approved_lines = "\n".join([f"- {item}" for item in constraints["use_instead"]]) or "- Use the grounded selectors from Available UI That Display After Test End."
            step_lines = "\n".join([f"- {item}" for item in constraints["steps"]]) or "- Replace every banned selector before returning the script."
            task_lines = _format_next_tasks(task_contract["next_tasks"]) or "- Follow NEXT_TASK_IDS from the task board."
            violation_text = (
                "## Planner Validation Failure\n"
                f"- Reused selectors: {', '.join(banned_hits) if banned_hits else 'none'}\n"
                f"- Invalid shortcut patterns: {', '.join(shortcut_hits) if shortcut_hits else 'none'}\n"
                "- You MUST regenerate the full JSON response.\n"
                "- Do not invent a new flow. Only patch the script to perform NEXT_TASK_IDS in order.\n\n"
                "## Immediate Tasks\n"
                f"{task_lines}\n\n"
                "## Approved Replacements\n"
                f"{approved_lines}\n\n"
                "## Required Fix Steps\n"
                f"{step_lines}\n\n"
                "## Invalid Draft\n"
                f"```javascript\n{test_script}\n```"
            )
            messages.append(HumanMessage(content=violation_text))
            response = await model.ainvoke(messages)
            test_script, intent = _parse_planner_response(response.content)

        print("* Intent :")
        print(intent)
        # print("* Script :")
        # print(test_script)
        # print("")

        return {
            "testcase_script": test_script,
        }
