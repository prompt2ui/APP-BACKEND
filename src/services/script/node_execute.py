import asyncio
import glob
import json
import os
import re
import shutil

from .state import AgentState
from ..llm import PROJECT_ROOT, TESTING_DIR, RESULT_DIR

# Playwright specs are written to TESTING_DIR (src/test/testing) with cwd=PROJECT_ROOT.
# Generated scripts import helpers via `../util.js` → src/test/util.js (same pattern as this folder’s examples).

class NodeExecute:

    @staticmethod
    def _sanitize_name(name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        return sanitized[:60] or "test"

    @staticmethod
    def _build_run_id(testcase_unique_id: str, attempt: int) -> str:
        return f"{testcase_unique_id}-round-{attempt}"

    @staticmethod
    async def run(state: AgentState) -> AgentState:
        test_script = state.get("testcase_script", "")
        testcase_unique_id = state.get("testcase_unique_id", "unnamed")
        attempt = state.get("execute_attempt", 0) + 1

        run_id = NodeExecute._build_run_id(testcase_unique_id, attempt)
        script_file = os.path.join(TESTING_DIR, f"{run_id}.js")
        result_dir = os.path.join(RESULT_DIR, run_id)

        # NOTE: cleanup moved to node_evaluate.py — happens AFTER evaluate reads the data

        os.makedirs(TESTING_DIR, exist_ok=True)
        os.makedirs(result_dir, exist_ok=True)

        with open(script_file, "w", encoding="utf-8") as f:
            f.write(test_script)


        try:
            proc = await asyncio.create_subprocess_exec(
                "bunx", "playwright", "test", script_file,
                "--reporter=line",
                f"--output={result_dir}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT_ROOT,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            stdout = ""
            stderr = "Playwright execution timed out after 120 seconds"
            exit_code = 1
        except Exception as e:
            stdout = ""
            stderr = str(e)
            exit_code = 1

        screenshot_path = NodeExecute._find_latest_file(result_dir, "current-ui.png") or NodeExecute._find_latest_file(result_dir, "*.png")
        metadata_logs = NodeExecute._read_json(result_dir, "metadata.json")
        console_logs = NodeExecute._read_json(result_dir, "console-logs.json")
        network_logs = NodeExecute._read_json(result_dir, "network-logs.json")
        event_logs = NodeExecute._read_json(result_dir, "event-logs.json")
        ui_elements = NodeExecute._read_json(result_dir, "current-dom.json")

        # Extract injected page context from util.js and remove so it doesn't pollute LLM element lists
        execute_current_url = ""
        execute_page_heading = ""
        if isinstance(ui_elements, dict):
            execute_current_url = ui_elements.pop("_url", "") or ""
            ui_elements.pop("_page_title", None)
            execute_page_heading = ui_elements.pop("_page_heading", "") or ""

        execute_log = NodeExecute._build_log(stdout, stderr, exit_code)
        execute_result = "pass" if exit_code == 0 else "fail"

        return {
            "execute_attempt": attempt,
            "execute_result": execute_result,
            "execute_log": execute_log,
            "execute_screenshot": screenshot_path,
            "execute_metadata_logs": metadata_logs,
            "execute_console_logs": console_logs,
            "execute_network_logs": network_logs,
            "execute_event_logs": event_logs,
            "execute_ui_elements": ui_elements,
            "execute_current_url": execute_current_url,
            "execute_page_heading": execute_page_heading,
        }

    @staticmethod
    def _cleanup_previous_round(testcase_unique_id: str, prev_attempt: int):
        """Delete script + result folder from the previous round (failed attempts only)."""
        if prev_attempt < 1:
            return
        prev_run_id = NodeExecute._build_run_id(testcase_unique_id, prev_attempt)
        prev_script = os.path.join(TESTING_DIR, f"{prev_run_id}.js")
        prev_result = os.path.join(RESULT_DIR, prev_run_id)
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
    def _find_latest_file(directory: str, pattern: str) -> str:
        full_pattern = os.path.join(directory, "**", pattern)
        files = glob.glob(full_pattern, recursive=True)
        if not files:
            files = glob.glob(os.path.join(directory, pattern))
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            return files[0]
        return ""

    @staticmethod
    def _read_json(directory: str, filename: str) -> dict:
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def _build_log(stdout: str, stderr: str, exit_code: int) -> str:
        parts = []
        if exit_code != 0:
            error_lines = NodeExecute._extract_error_lines(stdout, stderr)
            if error_lines:
                parts.append("## Error\n" + error_lines)
        if stdout:
            parts.append("## Stdout\n" + stdout[-3000:])
        if stderr:
            parts.append("## Stderr\n" + stderr[-1500:])
        parts.append(f"## Exit Code: {exit_code}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_error_lines(stdout: str, stderr: str) -> str:
        combined = stdout + "\n" + stderr
        lines = []
        capture = False
        for line in combined.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in ["error", "fail", "timeout", "expect("]):
                capture = True
            if capture:
                lines.append(line)
                if len(lines) >= 30:
                    break
        return "\n".join(lines) if lines else stderr[:2000]
