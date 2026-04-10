import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from .state import AgentState, MAX_ATTEMPTS, MAX_TESTCASE_CONCURRENCY
from .bootstrap_dom import capture_initial_ui
from .node_planner import NodePlanner
from .node_execute import NodeExecute
from .node_evaluate import NodeEvaluate
from ..llm import TESTING_DIR, RESULT_DIR
from ..case.graph import CaseAgentEntryPoint
from ..storage import DatabaseService, StorageService

DEBUG_CURRENT_TEST_DIR = os.path.join("debug", "output", "current_test")


def _format_testcase_unique_id(case_index: int) -> str:
    """Per test run: TC{index}_{6 hex chars}, e.g. TC1_a3f2c1 (index starts at 1)."""
    return f"TC{case_index}_{uuid.uuid4().hex[:6]}"


def _priority_db_value(raw: Any) -> str:
    """Prisma `TestcasePriority`: low | medium | high."""
    s = str(raw or "medium").strip().lower()
    return s if s in ("low", "medium", "high") else "medium"


def _initial_task_meta() -> dict[str, str]:
    return {
        "CURRENT_STEP": "INITIAL_PAGE_LOAD",
        "NEXT_TASK_IDS": "T001,T002,T003,T004",
        "PLANNER_MODE": "patch_existing_script_only",
        "COMPLETE_WHEN": "all required tasks are marked [x] and the current UI is a true terminal/final state for the testcase goal",
    }


def _initial_task_board(test_url: str) -> list[str]:
    return [
        f"[ ] T001 | event=goto | type=page | target=url | value={test_url} | required=true | note=Open the target web page",
        "[ ] T002 | event=observe | type=page | required=true | note=Inspect the initial visible UI and identify the current form step using grounded controls",
        "[ ] T003 | event=complete_current_step | type=form | required=true | note=Complete the current required step using visible labels, ids, roles, and validation messages from the live UI",
        "[ ] T004 | event=click_forward | type=button | required=true | note=Advance only after the current step is valid, then wait for the next step and continue from the updated UI",
    ]


def _format_task_meta(meta: dict[str, str]) -> str:
    ordered_keys = ("CURRENT_STEP", "NEXT_TASK_IDS", "PLANNER_MODE", "COMPLETE_WHEN")
    lines = []
    for key in ordered_keys:
        value = str(meta.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _resolve_round(node_name: str, input_state: dict, output_state: dict) -> int:
    attempt_in = input_state.get("execute_attempt", 0)
    attempt_out = output_state.get("execute_attempt", attempt_in)
    try:
        attempt_in = int(attempt_in)
    except Exception:
        attempt_in = 0
    try:
        attempt_out = int(attempt_out)
    except Exception:
        attempt_out = attempt_in

    if node_name == "planner":
        return max(1, attempt_in + 1)
    if node_name == "evaluate":
        return max(1, attempt_out)
    return max(1, attempt_out if attempt_out > 0 else attempt_in + 1)


def _write_node_debug(node_name: str, input_state: dict, output_state: dict) -> None:
    round_no = _resolve_round(node_name, input_state, output_state)
    os.makedirs(DEBUG_CURRENT_TEST_DIR, exist_ok=True)
    file_path = os.path.join(DEBUG_CURRENT_TEST_DIR, f"testcase_round_{round_no}.json")

    entry = {
        "round": round_no,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "node": node_name,
        "testcase_unique_id": output_state.get("testcase_unique_id") or input_state.get("testcase_unique_id"),
        "input": _json_safe(input_state),
        "output": _json_safe(output_state),
        "state": _json_safe(output_state),
    }

    payload: list[dict] = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                old = json.load(f)
                if isinstance(old, list):
                    payload = old
                elif isinstance(old, dict):
                    payload = [old]
        except Exception:
            payload = []

    payload.append(entry)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _with_debug_writer(node_name: str, node_fn):
    async def _runner(state: AgentState):
        input_state = dict(state)
        output_state = await node_fn(state)
        _write_node_debug(node_name, input_state, dict(output_state))
        return output_state

    return _runner


def _build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("planner", _with_debug_writer("planner", NodePlanner.run))
    graph.add_node("execute", _with_debug_writer("execute", NodeExecute.run))
    graph.add_node("evaluate", _with_debug_writer("evaluate", NodeEvaluate.run))

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "execute")
    graph.add_edge("execute", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        lambda state: "complete" if state.get("execute_result") == "complete" else "retry",
        {"retry": "planner", "complete": END},
    )

    return graph.compile()


_compiled_graph = _build_graph()

async def _stream_callback(queue: asyncio.Queue, event: dict) -> None:
    """Put event without blocking forever if client disconnected."""
    try:
        queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        # Drop oldest items until there is space.
        try:
            while queue.full():
                queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Give up if still full.
            return


def _build_initial_context(
    tc: dict,
    test_url: str,
    *,
    bootstrap_ui: dict | None = None,
    bootstrap_page_url: str = "",
    bootstrap_page_heading: str = "",
) -> str:
    """Build the initial testcase_context markdown from a testcase dict."""
    name = tc.get("testcase_name", "")
    desc = tc.get("testcase_description", "")
    cat = tc.get("testcase_category", "")
    prio = tc.get("testcase_priority", "Medium")
    task_meta = _initial_task_meta()
    task_board = _initial_task_board(test_url)
    page_url = (bootstrap_page_url or "").strip() or ""
    page_heading = (bootstrap_page_heading or "").strip() or ""
    context_map = {
        "goal": {
            "testcase_name": name,
            "testcase_description": desc,
            "testcase_category": cat,
            "testcase_priority": prio,
        },
        "latest_evaluation": {
            "round": 0,
            "status": "initial",
            "summary": "",
            "root_cause": "",
            "do_not_use": [],
            "use_instead": [],
            "steps": [],
            "planner_violation": "",
        },
        "page_state": {
            "url": page_url,
            "heading": page_heading,
            "label": "",
        },
        "task_meta": task_meta,
        "task_board": task_board,
        "testcase_suggestion": "",
        "testcase_result": "",
        "note": "Initial planning context before any execution attempt. UI snapshot is from bootstrap DOM capture (same util.js pipeline as post-run).",
    }

    parts = [
        f"## TEST CASE GOAL\n"
        f"- testcase_name: {name}\n"
        f"- testcase_description: {desc}\n"
        f"- testcase_category: {cat}\n"
        f"- testcase_priority: {prio}\n"
        f"\n## MACHINE READABLE CONTEXT\n"
        f"```json\n{json.dumps(context_map, ensure_ascii=False, indent=2)}\n```\n"
        f"\n## TASK META\n"
        f"{_format_task_meta(task_meta)}\n"
        f"\n## TASK\n"
        f"{chr(10).join(task_board)}\n",
    ]
    if bootstrap_ui:
        ui_text = NodeEvaluate._format_ui_elements(bootstrap_ui)
        if ui_text.strip():
            parts.extend(["\n## Available UI That Display After Test End\n", ui_text.rstrip(), "\n"])
    return "".join(parts)


class AgentEntryPoint:

    @staticmethod
    async def script_generate_stream(data: dict, event_queue: asyncio.Queue) -> dict:
        project_detail = data.get("project_detail", {})
        test_detail = data.get("test_detail", {})
        testcases = data.get("testcases", [])
        skip_db = data.get("skip_db", False)
        max_concurrency = MAX_TESTCASE_CONCURRENCY

        project_id = project_detail.get("project_id")
        test_url = test_detail.get("test_url", "")
        test_name = test_detail.get("test_name", "Untitled Test")

        db = DatabaseService()
        storage = StorageService()

        test_id = None
        if not skip_db:
            test_id = await db.create_test(project_id, test_detail)

        await _stream_callback(event_queue, {
            "type": "test_created",
            "test_id": test_id,
            "project_id": project_id,
            "test_name": test_name,
            "test_url": test_url,
            "total_testcases": len(testcases),
            "testcases": [
                {
                    "testcase_id": tc.get("testcase_id", ""),
                    "testcase_name": tc.get("testcase_name", ""),
                    "testcase_description": tc.get("testcase_description", ""),
                    "testcase_category": (tc.get("testcase_category") or "functional"),
                    "testcase_priority": tc.get("testcase_priority", "Medium"),
                    "testcase_status": "running",
                }
                for tc in testcases
            ],
        })

        bootstrap_ui: dict = {}
        bootstrap_page_url = ""
        bootstrap_page_heading = ""
        if test_url and str(test_url).strip():
            bootstrap_ui, bootstrap_page_url, bootstrap_page_heading = await capture_initial_ui(str(test_url).strip())

        semaphore = asyncio.Semaphore(min(max_concurrency, max(1, len(testcases))))
        results: list[dict] = []

        async def run_one(idx: int, tc: dict) -> dict:
            async with semaphore:
                tc_simple = {
                    "testcase_name": tc.get("testcase_name", ""),
                    "testcase_description": tc.get("testcase_description", ""),
                    "testcase_category": str(tc.get("testcase_category") or "").strip()
                    or "functional",
                    "testcase_priority": tc.get("testcase_priority", "Medium"),
                }

                tc_unique_id = _format_testcase_unique_id(idx + 1)

                await _stream_callback(event_queue, {
                    "type": "testcase_running",
                    "test_id": test_id,
                    "index": idx,
                    "testcase_name": tc_simple["testcase_name"],
                    "testcase_unique_id": tc_unique_id,
                })

                testcase_id = None
                if not skip_db and test_id:
                    try:
                        # Create testcase row first as `running`, then update to `completed` after generation.
                        running_detail = {
                            "testcase_unique_id": tc_unique_id,
                            "testcase": tc_simple,
                            "test_script": "",
                            "test_validation": "",
                            "testcase_priority": tc_simple.get("testcase_priority", "Medium"),
                        }
                        testcase_id = await db.create_testcase(test_id, running_detail)
                    except Exception as e:
                        print(f"[AgentGraph] create_testcase(running) error: {e}")

                initial_state: AgentState = {
                    "test_url": test_url,
                    "testcase": tc_simple,
                    "testcase_unique_id": tc_unique_id,
                    "testcase_script": "",
                    "testcase_context": _build_initial_context(
                        tc_simple,
                        test_url,
                        bootstrap_ui=bootstrap_ui or None,
                        bootstrap_page_url=bootstrap_page_url,
                        bootstrap_page_heading=bootstrap_page_heading,
                    ),
                    "testcase_result": "",
                    "execute_attempt": 0,
                    "execute_result": "",
                    "execute_log": "",
                    "execute_screenshot": "",
                    "execute_metadata_logs": {},
                    "execute_console_logs": {},
                    "execute_network_logs": {},
                    "execute_event_logs": {},
                    "execute_ui_elements": dict(bootstrap_ui) if bootstrap_ui else {},
                    "execute_current_url": bootstrap_page_url or "",
                    "execute_page_heading": bootstrap_page_heading or "",
                }

                try:
                    final_state = await _compiled_graph.ainvoke(initial_state)
                except Exception as e:
                    final_state = {**initial_state, "execute_result": "fail", "execute_log": str(e)}

                final_verdict = str(final_state.get("testcase_result", "") or "").lower()
                is_pass = final_verdict == "pass"

                tc_result = {
                    "testcase_unique_id": tc_unique_id,
                    "testcase": tc_simple,
                    "test_script": final_state.get("testcase_script", ""),
                    "test_validation": "pass" if is_pass else "fail",
                    "testcase_result": "success" if is_pass else "fail",
                    "testcase_suggestion": final_state.get("testcase_suggestion", "") or "",
                    "testcase_priority": tc_simple.get("testcase_priority", "Medium"),
                    "attempts": final_state.get("execute_attempt", 0),
                    "testcase_context": final_state.get("testcase_context", ""),
                    "error": "" if is_pass else final_state.get("execute_log", "")[:2000],
                }

                testcase_record = None
                if not skip_db and test_id:
                    try:
                        if testcase_id is None:
                            # Fallback: if running insert failed, create the completed row directly.
                            testcase_id = await db.create_testcase(test_id, tc_result)

                        tc_result["testcase_id"] = testcase_id

                        attempt = final_state.get("execute_attempt", 1) or 1
                        final_run_id = NodeExecute._build_run_id(tc_unique_id, attempt)
                        artifacts = await storage.upload_testcase_artifacts(
                            str(project_id), str(test_id), final_run_id
                        )
                        await db.update_testcase_logs(
                            testcase_id,
                            final_run_id,
                            artifacts,
                            testcase_script=final_state.get("testcase_script", ""),
                            test_validation=tc_result.get("test_validation"),
                            testcase_suggestion=final_state.get("testcase_suggestion") or "",
                        )
                        testcase_record = await db.get_testcase(testcase_id)
                    except Exception as e:
                        print(f"[AgentGraph] DB save error: {e}")

                outcome = "success" if is_pass else "fail"
                prio = _priority_db_value(tc_simple.get("testcase_priority", "Medium"))
                await _stream_callback(event_queue, {
                    "type": "testcase_complete",
                    "test_id": test_id,
                    "index": idx,
                    "testcase_name": tc_simple["testcase_name"],
                    "testcase_result": outcome,
                    "testcase_status": "completed",
                    "testcase_piority": prio,
                    "testcase": testcase_record
                    or {
                        "testcase_id": tc_result.get("testcase_id", -(idx + 1)),
                        "test_id": test_id,
                        "testcase_unique_id": tc_unique_id,
                        "testcase_name": tc_simple["testcase_name"],
                        "testcase_description": tc_simple["testcase_description"],
                        "testcase_category": tc_simple.get("testcase_category") or "functional",
                        "testcase_piority": prio,
                        "testcase_result": outcome,
                        "testcase_status": "completed",
                        "testcase_script": final_state.get("testcase_script", ""),
                        "testcase_suggestion": final_state.get("testcase_suggestion", "") or "",
                    },
                })
                return tc_result

        if testcases:
            results = list(await asyncio.gather(*(run_one(idx, tc) for idx, tc in enumerate(testcases))))

        # Persist Test.test_status = completed (Prisma TestcaseStatus) when the run finishes.
        if not skip_db and test_id:
            try:
                await db.update_test_completed(test_id)
            except Exception as e:
                print(f"[AgentGraph] update_test_completed error: {e}")

        output = {
            "test_id": test_id,
            "project_id": project_id,
            "test_name": test_name,
            "test_status": "completed",
            "test_url": test_url,
            "testcases": results,
        }

        if not skip_db and test_id:
            fresh_test = await db.get_test(test_id)
            if fresh_test:
                output = fresh_test

        await _stream_callback(event_queue, {"type": "done", "test": output})
        return output

    @staticmethod
    async def case_generate(data: dict) -> dict:
        return await CaseAgentEntryPoint.case_generate(data)

    @staticmethod
    async def case_draft(data: dict) -> dict:
        return await CaseAgentEntryPoint.case_draft(data)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        return CaseAgentEntryPoint._parse_json(raw)

