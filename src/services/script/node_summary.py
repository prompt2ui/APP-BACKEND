import asyncio
import json
import os
import time
from datetime import datetime
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

from src.config import env

_MAX_VIDEO_PREFETCH_BYTES = 500 * 1024 * 1024

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
SUMMARY_DIR = os.path.join(PROJECT_ROOT, "src", "test", "test-summary")
SUMMARY_TEMPLATE_DIR = os.path.join(SUMMARY_DIR, "template")
SUMMARY_TEMPLATE_NAME = "template.html"
SUMMARY_OUTPUT_DIR = os.path.join(SUMMARY_DIR, "output")


def _log_summary(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [Summary] {message}", flush=True)


def _normalize_result(value: object) -> str:
    return "pass" if str(value or "").lower() in {"pass", "success"} else "fail"


def _build_summary_data(test_info: dict, summarized_testcases: list[dict]) -> dict:
    total = len(summarized_testcases)
    passed = sum(1 for tc in summarized_testcases if tc.get("testcase_result") == "PASS")
    failed = total - passed
    pass_rate = f"{(passed / total * 100):.1f}%" if total > 0 else "0%"
    return {
        "project_name": test_info.get("project_name", "N/A"),
        "test_name": test_info.get("test_name", "N/A"),
        "test_url": test_info.get("test_url", "N/A"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
    }


class MarkdownSummaryAgent:
    SUMMARY_MODEL = "gpt-4.1-mini"
    MAX_SCRIPT_CHARS = 5000
    MAX_SCRIPT_SNIPPET_MD = 3500

    @classmethod
    async def build(cls, data: dict, *, prefetch_videos: bool = False) -> dict:
        test_info = data.get("test") or {}
        testcases = data.get("testcases") or []
        export_destination = (data.get("export_destination") or "summary").strip().lower()
        project_id = str(test_info.get("project_id", "unknown"))
        test_id = str(test_info.get("test_id", "unknown"))

        _log_summary(
            f"markdown start test={test_info.get('test_name')!r} project_id={project_id} "
            f"test_id={test_id} testcases={len(testcases)} destination={export_destination!r} "
            f"prefetch_videos={prefetch_videos}"
        )

        started_at = time.monotonic()
        client = AsyncOpenAI(api_key=env.OPENAI_API_KEY, timeout=120.0, max_retries=1)
        summarized_testcases = await asyncio.gather(
            *(cls._summarize_testcase(client, testcase, prefetch_videos) for testcase in testcases)
        )
        _log_summary(f"markdown testcase summarization done in {time.monotonic() - started_at:.1f}s")

        summary_data = _build_summary_data(test_info, summarized_testcases)

        video_prefetch_by_url: dict[str, bytes] = {}
        for testcase in summarized_testcases:
            video_bytes = testcase.pop("_video_prefetch_bytes", None)
            video_url = (testcase.get("testcase_video") or "").strip()
            if video_url and isinstance(video_bytes, (bytes, bytearray)) and len(video_bytes) > 0:
                video_prefetch_by_url[video_url] = bytes(video_bytes)

        return {
            "project_id": project_id,
            "test_id": test_id,
            "summary_data": summary_data,
            "summarized_testcases": summarized_testcases,
            "video_prefetch_by_url": video_prefetch_by_url,
            "clickup_video_prefetch": video_prefetch_by_url,
        }

    @classmethod
    async def _summarize_testcase(
        cls,
        client: AsyncOpenAI,
        testcase: dict,
        prefetch_videos: bool,
    ) -> dict:
        testcase_name = testcase.get("testcase_name")
        video_url = (testcase.get("testcase_video") or "").strip()

        _log_summary(f"markdown testcase start: {testcase_name}")
        if prefetch_videos and video_url:
            ai_summary, video_bytes = await asyncio.gather(
                cls._get_ai_summary(client, testcase),
                cls._download_video_prefetch(video_url),
            )
        else:
            ai_summary = await cls._get_ai_summary(client, testcase)
            video_bytes = None

        normalized_result = _normalize_result(testcase.get("testcase_result"))
        script = testcase.get("testcase_script") or ""
        preview = script[:800] + "..." if len(script) > 800 else script
        snippet_limit = cls.MAX_SCRIPT_SNIPPET_MD
        snippet = script[:snippet_limit] + "\n...(truncated)" if len(script) > snippet_limit else script

        row = {
            "testcase_name": testcase_name,
            "testcase_unique_id": testcase.get("testcase_unique_id"),
            "testcase_description": testcase.get("testcase_description"),
            "testcase_result": normalized_result.upper(),
            "result_class": "result-pass" if normalized_result == "pass" else "result-fail",
            "testcase_status": testcase.get("testcase_status"),
            "testcase_priority": testcase.get("testcase_piority", "low"),
            "summary_title": ai_summary.get("summary_title", testcase_name),
            "summary_text": ai_summary.get("summary_text", ""),
            "key_issue": ai_summary.get("key_issue", "N/A"),
            "recommendation": ai_summary.get("recommendation", "N/A"),
            "testcase_script_preview": preview if script else None,
            "testcase_script_snippet": snippet.strip() if script else None,
            "testcase_video": video_url or None,
        }
        if prefetch_videos and video_url and video_bytes:
            row["_video_prefetch_bytes"] = video_bytes

        _log_summary(f"markdown testcase done: {testcase_name}")
        return row

    @classmethod
    async def _get_ai_summary(cls, client: AsyncOpenAI, testcase: dict) -> dict:
        script = testcase.get("testcase_script") or ""
        if len(script) > cls.MAX_SCRIPT_CHARS:
            script = script[: cls.MAX_SCRIPT_CHARS] + "...(truncated)"

        result_for_prompt = _normalize_result(testcase.get("testcase_result"))
        prompt = f"""
Summarize the following automated test case result in Thai.
Testcase Name: {testcase.get('testcase_name')}
Description: {testcase.get('testcase_description')}
Result: {result_for_prompt}
Status: {testcase.get('testcase_status')}
Script:
{script}

Provide the response in JSON format (Thai language) with the following keys:
- summary_title: A short catchy title for this specific test case result
- summary_text: A concise summary of what was tested and the outcome
- key_issue: The main problem identified (if failed) or "None" (if passed)
- recommendation: Actionable suggestion for the developers or QA
"""
        try:
            response = await client.chat.completions.create(
                model=cls.SUMMARY_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert QA Automation Engineer. "
                            "You provide clear, professional summaries in Thai."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                timeout=120.0,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            print(f"Error getting AI summary for {testcase.get('testcase_name')}: {exc}")
            return {
                "summary_title": testcase.get("testcase_name"),
                "summary_text": "ไม่สามารถสร้างสรุปได้เนื่องจากข้อผิดพลาดทางเทคนิค",
                "key_issue": "Error in AI processing",
                "recommendation": "โปรดตรวจสอบ Log ของระบบ",
            }

    @staticmethod
    async def _download_video_prefetch(video_url: str) -> Optional[bytes]:
        if not video_url or not video_url.lower().startswith(("http://", "https://")):
            return None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0),
                follow_redirects=True,
            ) as client:
                response = await client.get(video_url)
                response.raise_for_status()
                body = response.content
                if len(body) > _MAX_VIDEO_PREFETCH_BYTES:
                    print(f"[AI-Summary] video prefetch skip (too large): {video_url[:80]}…", flush=True)
                    return None
                return body
        except Exception as exc:
            print(f"[AI-Summary] video prefetch failed {video_url[:80]}… — {exc}", flush=True)
            return None


class PdfSummaryAgent:
    @classmethod
    async def build(cls, data: dict) -> dict:
        export_destination = (data.get("export_destination") or "summary").strip().lower()
        markdown_result = await MarkdownSummaryAgent.build(data, prefetch_videos=False)
        project_id = markdown_result["project_id"]
        test_id = markdown_result["test_id"]
        summary_data = markdown_result["summary_data"]
        summarized_testcases = markdown_result["summarized_testcases"]

        output_dir = os.path.join(SUMMARY_OUTPUT_DIR, project_id, test_id)
        os.makedirs(output_dir, exist_ok=True)

        _log_summary(
            f"pdf start project_id={project_id} test_id={test_id} destination={export_destination!r}"
        )
        render_started_at = time.monotonic()
        template_env = Environment(loader=FileSystemLoader(SUMMARY_TEMPLATE_DIR))
        template = template_env.get_template(SUMMARY_TEMPLATE_NAME)
        html_content = template.render(summary=summary_data, testcases=summarized_testcases)
        _log_summary(f"pdf html render done in {time.monotonic() - render_started_at:.1f}s")

        filename = f"summary_{test_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf_path = os.path.join(output_dir, filename)
        await cls._write_pdf(html_content, pdf_path)

        return {
            "pdf_path": pdf_path,
            "filename": filename,
            "project_id": project_id,
            "test_id": test_id,
            "summary_data": summary_data,
            "summarized_testcases": summarized_testcases,
        }

    @staticmethod
    async def _write_pdf(html_content: str, pdf_path: str) -> None:
        started_at = time.monotonic()
        _log_summary(f"pdf playwright start -> {pdf_path}")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html_content, wait_until="load")
            await page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            )
            await browser.close()
        _log_summary(f"pdf playwright done in {time.monotonic() - started_at:.1f}s")


class NodeSummary:
    @staticmethod
    async def create_markdown_summary(data: dict) -> dict:
        prefetch_videos = bool(data.get("prefetch_videos") or data.get("skip_pdf"))
        return await MarkdownSummaryAgent.build(data, prefetch_videos=prefetch_videos)

    @staticmethod
    async def create_pdf_summary(data: dict) -> dict:
        return await PdfSummaryAgent.build(data)

    @staticmethod
    async def case_summary(data: dict) -> dict:
        if bool(data.get("skip_pdf")):
            return await NodeSummary.create_markdown_summary(data)
        return await NodeSummary.create_pdf_summary(data)
