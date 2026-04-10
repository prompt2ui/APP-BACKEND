"""
Capture initial page DOM using the same util.js path as generated Playwright tests (saveCurrentDom).
Populates execute_ui_elements before round 1 so the planner sees grounded controls.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid

from ..llm import PROJECT_ROOT, TEST_ROOT


def _strip_meta_keys(grouped: dict) -> tuple[dict, str, str]:
    """Match node_execute: split _url / _page_heading for state fields; return clean dict for evaluator format."""
    if not isinstance(grouped, dict):
        return {}, "", ""
    data = dict(grouped)
    url = str(data.pop("_url", "") or "").strip()
    data.pop("_page_title", None)
    heading = str(data.pop("_page_heading", "") or "").strip()
    return data, url, heading


async def capture_initial_ui(test_url: str, timeout_sec: float = 120.0) -> tuple[dict, str, str]:
    """
    Run bootstrap-dom-capture.mjs via bun. Returns (execute_ui_elements, execute_current_url, execute_page_heading).
    On failure returns ({}, "", "").
    """
    url = (test_url or "").strip()
    if not url:
        return {}, "", ""

    run_id = f"bootstrap-{uuid.uuid4().hex[:12]}"
    out_dir = os.path.join(TEST_ROOT, "test-result", run_id)
    os.makedirs(out_dir, exist_ok=True)

    script_path = os.path.join(TEST_ROOT, "bootstrap-dom-capture.mjs")
    env = {
        **os.environ,
        "BOOTSTRAP_URL": url,
        "BOOTSTRAP_OUT_DIR": out_dir,
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            "bun",
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=PROJECT_ROOT,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0:
            err = (stderr_b or b"").decode("utf-8", errors="replace")[:2000]
            out = (stdout_b or b"").decode("utf-8", errors="replace")[:500]
            print(f"[bootstrap_dom] capture failed rc={proc.returncode} stderr={err!r} stdout={out!r}")
            return {}, "", ""

        json_path = os.path.join(out_dir, "current-dom.json")
        if not os.path.isfile(json_path):
            print("[bootstrap_dom] missing current-dom.json")
            return {}, "", ""

        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}, "", ""

        clean, page_url, heading = _strip_meta_keys(raw)
        return clean, page_url or url, heading
    except asyncio.TimeoutError:
        print(f"[bootstrap_dom] capture timed out after {timeout_sec}s")
        return {}, "", ""
    except Exception as e:
        print(f"[bootstrap_dom] capture error: {e}")
        return {}, "", ""
    finally:
        try:
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir, ignore_errors=True)
        except OSError:
            pass
