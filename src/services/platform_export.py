# src/services/platform_export.py
import asyncio
import base64
import glob
import html as html_lib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .clickup_config import resolve_clickup_list_id
from .provider_store import get_provider_key

CLICKUP_API = "https://api.clickup.com/api/v2"
GITHUB_REST_API = "https://api.github.com"
CLICKUP_MAX_VIDEO_ATTACHMENT_BYTES = 500 * 1024 * 1024
# GitHub Contents API: very large files are rejected or unwieldy; stay under the doc guidance (~100 MiB).
GITHUB_MAX_REPO_FILE_BYTES = 95 * 1024 * 1024
GITHUB_PREVIEW_GIF_MAX_BYTES = 8 * 1024 * 1024
CLICKUP_HTTP_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)


def _attachment_filename(video_url: str, testcase_name: str) -> str:
    path = urlparse(video_url).path
    base = os.path.basename(path) or ""
    if base and "." in base:
        return base[:200]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (testcase_name or "video"))[:80]
    # Playwright default recording is WebM; prefer that when the URL has no extension.
    return f"{safe or 'video'}.webm"


def _sniff_video_extension(file_bytes: bytes) -> str | None:
    """Best-effort magic-byte sniff so uploads get a real video extension / MIME for Jira preview."""
    if len(file_bytes) < 12:
        return None
    if file_bytes[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if file_bytes[4:8] == b"ftyp":
        return ".mp4"
    return None


def _jira_normalize_video_filename(filename: str, file_bytes: bytes) -> str:
    sniffed = _sniff_video_extension(file_bytes)
    if not sniffed:
        return filename
    root, ext = os.path.splitext(filename)
    ext_lower = ext.lower()
    if sniffed == ".webm" and ext_lower not in (".webm",):
        return f"{root or 'recording'}.webm"
    if sniffed == ".mp4" and ext_lower == ".webm":
        return f"{root or 'recording'}.mp4"
    if not ext_lower or ext_lower not in (".webm", ".mp4", ".mov", ".mkv", ".avi"):
        return f"{root or 'recording'}{sniffed}"
    return filename


def _video_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }.get(ext, "application/octet-stream")


def _github_safe_path_segment(name: str, max_len: int = 72) -> str:
    segment = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "testcase"))[:max_len]
    return segment or "testcase"


def _resolve_ffmpeg_executable() -> str | None:
    """
    Prefer explicit env, then PATH, then Playwright's downloaded ffmpeg (same as `npx playwright install ffmpeg`).
    """
    for env_key in ("FFMPEG_PATH", "PLAYWRIGHT_FFMPEG_PATH"):
        raw = (os.environ.get(env_key) or "").strip()
        if not raw:
            continue
        if os.path.isfile(raw) and os.access(raw, os.X_OK):
            return raw
        resolved = shutil.which(raw)
        if resolved:
            return resolved
    which = shutil.which("ffmpeg")
    if which:
        return which
    home = os.path.expanduser("~")
    cache_bases: list[str] = []
    pw = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if pw:
        cache_bases.append(pw)
    if sys.platform == "darwin":
        cache_bases.append(os.path.join(home, "Library", "Caches", "ms-playwright"))
    else:
        xdg = (os.environ.get("XDG_CACHE_HOME") or "").strip()
        cache_bases.append(os.path.join(xdg or os.path.join(home, ".cache"), "ms-playwright"))
    machine = platform.machine().lower()
    if sys.platform == "win32":
        exe_names = ("ffmpeg-win64.exe", "ffmpeg.exe")
    elif sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            exe_names = ("ffmpeg-mac-arm64", "ffmpeg-mac")
        else:
            exe_names = ("ffmpeg-mac", "ffmpeg-mac-arm64")
    else:
        if "arm" in machine or "aarch64" in machine:
            exe_names = ("ffmpeg-linux-arm64", "ffmpeg-linux")
        else:
            exe_names = ("ffmpeg-linux", "ffmpeg-linux-arm64")
    for base in cache_bases:
        if not base or not os.path.isdir(base):
            continue
        for ffmpeg_home in sorted(glob.glob(os.path.join(base, "ffmpeg-*")), reverse=True):
            for name in exe_names:
                candidate = os.path.join(ffmpeg_home, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
                if sys.platform == "win32" and os.path.isfile(candidate):
                    return candidate
    return None


def _ffmpeg_transcode_bytes_to_mp4(input_bytes: bytes, input_suffix: str) -> bytes | None:
    """Transcode video bytes to H.264/AAC MP4 for GitHub (WebM and similar). Returns None on failure."""
    ffmpeg_bin = _resolve_ffmpeg_executable()
    if not ffmpeg_bin:
        print(
            "[GitHub] ffmpeg not found. Options: (1) macOS: `brew install ffmpeg` "
            "(2) from app-backend: `npx playwright install ffmpeg` "
            "(3) set `FFMPEG_PATH` to the ffmpeg binary.",
            flush=True,
        )
        return None
    in_path: str | None = None
    out_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=input_suffix or ".webm", delete=False) as tmp_in:
            tmp_in.write(input_bytes)
            in_path = tmp_in.name
        out_fd, out_path = tempfile.mkstemp(suffix=".mp4")
        os.close(out_fd)
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                in_path,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                out_path,
            ],
            capture_output=True,
            timeout=600,
        )
        if proc.returncode != 0 or not os.path.isfile(out_path):
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:900]
            print(f"[GitHub] ffmpeg failed ({proc.returncode}): {err}", flush=True)
            return None
        with open(out_path, "rb") as tmp_out:
            return tmp_out.read()
    except Exception as exc:
        print(f"[GitHub] ffmpeg error: {exc}", flush=True)
        return None
    finally:
        for path in (in_path, out_path):
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _ffmpeg_extract_preview_gif(video_bytes: bytes, input_suffix: str) -> bytes | None:
    """Animated GIF from the recording, starting at **2s** (fallback 0s if clip is short)."""
    ffmpeg_bin = _resolve_ffmpeg_executable()
    if not ffmpeg_bin:
        return None
    in_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=input_suffix or ".webm", delete=False) as tmp_in:
            tmp_in.write(video_bytes)
            in_path = tmp_in.name

        for ss in ("2", "0"):
            for duration, width, fps in ((3.5, 400, 8), (2.5, 320, 7), (2.0, 260, 6)):
                out_fd, out_path = tempfile.mkstemp(suffix=".gif")
                os.close(out_fd)
                try:
                    proc = subprocess.run(
                        [
                            ffmpeg_bin,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-ss",
                            ss,
                            "-t",
                            str(duration),
                            "-i",
                            in_path,
                            "-vf",
                            f"fps={fps},scale={width}:-1:flags=lanczos",
                            "-loop",
                            "0",
                            out_path,
                        ],
                        capture_output=True,
                        timeout=180,
                    )
                    if proc.returncode != 0 or not os.path.isfile(out_path):
                        continue
                    if os.path.getsize(out_path) <= 64:
                        continue
                    with open(out_path, "rb") as gf:
                        data = gf.read()
                    if len(data) <= GITHUB_PREVIEW_GIF_MAX_BYTES:
                        return data
                finally:
                    if os.path.isfile(out_path):
                        try:
                            os.unlink(out_path)
                        except OSError:
                            pass
        return None
    except Exception:
        return None
    finally:
        if in_path and os.path.isfile(in_path):
            try:
                os.unlink(in_path)
            except OSError:
                pass


def _github_prepare_recording_file(filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """
    GitHub’s issue UI is built around MP4/MOV. WebM is transcoded to MP4 when possible.
    Returns (bytes, repo_filename basename) for the Contents API.
    """
    base = os.path.basename(filename)[:200] or "recording.webm"
    root, ext = os.path.splitext(base)
    ext_lower = ext.lower()
    needs_mp4 = ext_lower == ".webm" or (
        ext_lower not in (".mp4", ".mov", ".m4v") and _sniff_video_extension(file_bytes) == ".webm"
    )
    if needs_mp4:
        mp4_bytes = _ffmpeg_transcode_bytes_to_mp4(file_bytes, ext_lower or ".webm")
        if mp4_bytes:
            stem = (root or "recording")[:160]
            return mp4_bytes, f"{stem}.mp4"
        if ext_lower == ".webm":
            return file_bytes, base
    if ext_lower in (".mp4", ".mov", ".m4v"):
        return file_bytes, base
    return file_bytes, base


async def _download_bytes_from_url(video_url: str) -> bytes | None:
    if not video_url.lower().startswith(("http://", "https://")):
        return None
    async with httpx.AsyncClient(timeout=CLICKUP_HTTP_TIMEOUT, follow_redirects=True) as http_client:
        http_response = await http_client.get(video_url)
        http_response.raise_for_status()
        body = http_response.content
    if len(body) > CLICKUP_MAX_VIDEO_ATTACHMENT_BYTES:
        return None
    return body


async def _clickup_attach_file(
    *,
    api_token: str,
    clickup_task_id: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    upload_url = f"{CLICKUP_API}/task/{clickup_task_id}/attachment"
    files = {"attachment": (filename, file_bytes, "application/octet-stream")}
    async with httpx.AsyncClient(timeout=CLICKUP_HTTP_TIMEOUT) as http_client:
        http_response = await http_client.post(
            upload_url,
            headers={"Authorization": api_token},
            files=files,
        )
        http_response.raise_for_status()


def _markdown_href(url: str) -> str:
    """Wrap URL in <> when it contains characters that break markdown links."""
    if any(ch in url for ch in (" ", "(", ")")):
        u = url.replace("\n", "").strip()
        return f"<{u}>"
    return url


def _github_click_href_from_test_record(test_record: dict[str, Any]) -> str | None:
    u = str(test_record.get("test_url") or "").strip()
    if u.lower().startswith(("http://", "https://")):
        return u
    return None


def _github_blob_raw_display_url(blob_html_url: str) -> str | None:
    """Append ``?raw=true`` to a ``github.com/.../blob/...`` URL so ``<img src>`` loads bytes like the user's example."""
    u = (blob_html_url or "").strip()
    if not u or "github.com" not in u:
        return None
    if "raw=true" in u:
        return u
    sep = "&" if "?" in u else "?"
    return f"{u}{sep}raw=true"


def _github_blob_branch_raw_url(repository_full_name: str, branch: str, repo_path: str) -> str:
    try:
        owner, repo = repository_full_name.split("/", 1)
    except ValueError:
        owner, repo = repository_full_name, ""
    enc = _github_encode_repository_path_for_url(repo_path)
    return f"https://github.com/{owner}/{repo}/blob/{branch}/{enc}?raw=true"


def _github_clickable_preview_gif_html(img_src: str, link_href: str, alt: str) -> str:
    """GIF preview (blob ``?raw=true`` img); link opens ``link_href`` (original recording URL when available)."""
    safe_alt = html_lib.escape((alt or "Recording")[:200], quote=True)
    return (
        f'<a href="{html_lib.escape(link_href, quote=True)}">\n'
        f'  <img src="{html_lib.escape(img_src, quote=True)}" alt="{safe_alt}" width="100%" />\n'
        f"</a>"
    )


def _markdown_from_test(
    test: dict[str, Any],
    testcases: list[dict[str, Any]],
    *,
    github_video_embeds: dict[int, dict[str, str]] | None = None,
) -> str:
    """Beautiful Markdown from DB-shaped test + testcases.
    For GitHub export only: github_video_embeds maps testcase **list index** (0-based) to
    ``gif_src`` (blob ``?raw=true`` URL), ``click_url`` (usually ``testcase_video``), plus ``gif_raw`` / ``video_raw``.
    """
    test_title = test.get("test_name") or "Test"
    project_name = test.get("project_name") or "—"
    test_url = test.get("test_url") or "—"
    testcase_count = len(testcases)
    passed_count = sum(
        1 for testcase in testcases if str(testcase.get("testcase_result", "")).lower() == "success"
    )
    failed_count = testcase_count - passed_count
    pass_rate = f"{(passed_count / testcase_count * 100):.1f}%" if testcase_count else "0%"

    lines = [
        f"# 🧪 {test_title}",
        "",
        f"**Project:** 🏢 {project_name}",
        f"**Test URL:** 🌐 {test_url}",
        "",
        "### 📊 Execution Summary",
        "> Here is the summary of the test execution results.",
        "",
        "| Metric | Result |",
        "| :--- | :--- |",
        f"| **Total Test Cases** | {testcase_count} |",
        f"| **Passed** ✅ | {passed_count} |",
        f"| **Failed** ❌ | {failed_count} |",
        f"| **Pass Rate** 📈 | {pass_rate} |",
        "",
        "---",
        "",
        "### 📋 Detailed Test Cases",
        "",
    ]
    for i, testcase in enumerate(testcases, 1):
        testcase_name = testcase.get("testcase_name") or "Testcase"
        
        raw_result = str(testcase.get("testcase_result") or "—")
        is_success = raw_result.lower() == "success"
        result_emoji = "✅" if is_success else ("❌" if raw_result.lower() in ("failed", "fail", "error") else "⚠️")
        
        testcase_status = testcase.get("testcase_status") or "—"
        description = (testcase.get("testcase_description") or "").strip()
        testcase_script = (testcase.get("testcase_script") or "").strip()
        testcase_video_url = (testcase.get("testcase_video") or "").strip()

        lines.append(f"#### {i}. {testcase_name}")
        lines.append("")
        lines.append(f"- **Result:** {result_emoji} `{raw_result}`")
        lines.append(f"- **Status:** `{testcase_status}`")
        lines.append("")
        if description:
            lines.append("**Description:**")
            lines.append(f"> {description}")
            lines.append("")
        if testcase_script:
            lines.append("**Test Script:**")
            lines.append("```javascript")
            lines.append(testcase_script[:12000])
            lines.append("```")
            lines.append("")
        if testcase_video_url:
            lines.append("**Test Video:**")
            list_idx = i - 1
            embed = (
                github_video_embeds.get(list_idx)
                if github_video_embeds and list_idx in github_video_embeds
                else None
            )
            img_u = (embed.get("gif_src") or embed.get("gif_raw") or "").strip() if embed else ""
            link_u = (embed.get("click_url") or embed.get("video_raw") or "").strip() if embed else ""
            if testcase_video_url.lower().startswith(("http://", "https://")):
                link_u = testcase_video_url
            if embed and embed.get("video_raw") and img_u and link_u:
                lines.append("")
                tc_title = (testcase_name or "Recording").replace("]", "").replace("[", "")[:180]
                lines.append(_github_clickable_preview_gif_html(img_u, link_u, tc_title))
            else:
                lines.append(f"[{testcase_video_url}]({testcase_video_url})")
            lines.append("")

        if i < testcase_count:
            lines.append("---")
            lines.append("")

    return "\n".join(lines).strip()


def _jira_basic_auth_header(atlassian_email: str, api_token: str) -> str:
    credentials_utf8 = f"{atlassian_email}:{api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials_utf8).decode("ascii")


def _jira_issue_description_text_node(plain_text: str) -> dict[str, Any]:
    """One Atlassian Document Format text node (Jira issue description is not Markdown)."""
    truncated = (plain_text or "")[:32767]
    if not truncated:
        truncated = " "
    return {"type": "text", "text": truncated}


def _jira_issue_description_paragraph(*inline_nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(inline_nodes)}


def _jira_issue_description_heading(level: int, heading_text: str) -> dict[str, Any]:
    heading_level = max(1, min(level, 3))
    return {
        "type": "heading",
        "attrs": {"level": heading_level},
        "content": [_jira_issue_description_text_node(heading_text)],
    }


def _jira_issue_description_bullet_list_item(line_text: str) -> dict[str, Any]:
    return {
        "type": "listItem",
        "content": [_jira_issue_description_paragraph(_jira_issue_description_text_node(line_text))],
    }


def _jira_issue_description_paragraph_with_hyperlink(label_text: str, hyperlink_url: str) -> dict[str, Any]:
    """Paragraph: optional label plus a clickable URL (Atlassian Document Format link mark)."""
    hyperlink_url = (hyperlink_url or "").strip()[:32767]
    if not hyperlink_url:
        return _jira_issue_description_paragraph(_jira_issue_description_text_node(label_text))
    return {
        "type": "paragraph",
        "content": [
            _jira_issue_description_text_node(label_text),
            {
                "type": "text",
                "text": hyperlink_url,
                "marks": [{"type": "link", "attrs": {"href": hyperlink_url}}],
            },
        ],
    }


def _jira_issue_description_code_block(language_name: str, source_code: str) -> dict[str, Any]:
    code_body = (source_code or "")[:12000]
    return {
        "type": "codeBlock",
        "attrs": {"language": language_name or "text"},
        "content": [{"type": "text", "text": code_body or " "}],
    }


def _build_jira_issue_description_atlassian_document(
    test: dict[str, Any], testcases: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Build Jira Cloud REST API v3 `fields.description` as native Atlassian Document Format.
    ClickUp continues to use Markdown via `_markdown_from_test`; Jira does not render Markdown here.
    """
    test_title = test.get("test_name") or "Test"
    project_name = test.get("project_name") or "—"
    test_url = (test.get("test_url") or "").strip() or "—"
    testcase_count = len(testcases)
    passed_count = sum(
        1
        for testcase in testcases
        if str(testcase.get("testcase_result", "")).lower() == "success"
    )
    failed_count = testcase_count - passed_count
    pass_rate = f"{(passed_count / testcase_count * 100):.1f}%" if testcase_count else "0%"

    document_content_blocks: list[dict[str, Any]] = [
        _jira_issue_description_heading(1, f"🧪 {test_title}"),
        _jira_issue_description_paragraph(_jira_issue_description_text_node(f"Project: {project_name}")),
    ]
    if test_url != "—" and test_url.lower().startswith(("http://", "https://")):
        document_content_blocks.append(_jira_issue_description_paragraph_with_hyperlink("Test URL: ", test_url))
    else:
        document_content_blocks.append(
            _jira_issue_description_paragraph(_jira_issue_description_text_node(f"Test URL: {test_url}"))
        )

    document_content_blocks.append(_jira_issue_description_heading(2, "Execution summary"))
    document_content_blocks.append(
        {
            "type": "bulletList",
            "content": [
                _jira_issue_description_bullet_list_item(f"Total test cases: {testcase_count}"),
                _jira_issue_description_bullet_list_item(f"Passed: {passed_count}"),
                _jira_issue_description_bullet_list_item(f"Failed: {failed_count}"),
                _jira_issue_description_bullet_list_item(f"Pass rate: {pass_rate}"),
            ],
        }
    )

    document_content_blocks.append(_jira_issue_description_heading(2, "Detailed test cases"))

    for testcase_index, testcase in enumerate(testcases, 1):
        testcase_name = testcase.get("testcase_name") or "Testcase"
        testcase_result_raw = str(testcase.get("testcase_result") or "—")
        testcase_status = testcase.get("testcase_status") or "—"
        testcase_description = (testcase.get("testcase_description") or "").strip()
        testcase_script = (testcase.get("testcase_script") or "").strip()
        testcase_video_url = (testcase.get("testcase_video") or "").strip()

        document_content_blocks.append(_jira_issue_description_heading(3, f"{testcase_index}. {testcase_name}"))
        document_content_blocks.append(
            _jira_issue_description_paragraph(
                _jira_issue_description_text_node(
                    f"Result: {testcase_result_raw}  ·  Status: {testcase_status}"
                )
            )
        )
        if testcase_description:
            document_content_blocks.append(
                _jira_issue_description_paragraph(_jira_issue_description_text_node("Description"))
            )
            for description_line in testcase_description.split("\n"):
                stripped_line = description_line.strip()
                if stripped_line:
                    document_content_blocks.append(
                        _jira_issue_description_paragraph(
                            _jira_issue_description_text_node(stripped_line[:32767])
                        )
                    )
        if testcase_script:
            document_content_blocks.append(
                _jira_issue_description_paragraph(_jira_issue_description_text_node("Test script"))
            )
            document_content_blocks.append(_jira_issue_description_code_block("javascript", testcase_script))
        if testcase_video_url:
            document_content_blocks.append(
                _jira_issue_description_paragraph(
                    _jira_issue_description_text_node(
                        "Screen recording: attached to this issue (see the Attachments section on this ticket)."
                    )
                )
            )
            if testcase_video_url.lower().startswith(("http://", "https://")):
                document_content_blocks.append(
                    _jira_issue_description_paragraph_with_hyperlink("Original recording URL: ", testcase_video_url)
                )
            else:
                document_content_blocks.append(
                    _jira_issue_description_paragraph(_jira_issue_description_text_node(testcase_video_url))
                )

        if testcase_index < testcase_count:
            document_content_blocks.append({"type": "rule"})

    return {"type": "doc", "version": 1, "content": document_content_blocks}


async def _jira_create_issue(
    *,
    site_base_url: str,
    authorization_header: str,
    project_key: str,
    summary: str,
    issue_description_atlassian_document: dict[str, Any],
) -> dict[str, Any]:
    create_issue_url = f"{site_base_url.rstrip('/')}/rest/api/3/issue"
    request_headers = {"Authorization": authorization_header, "Content-Type": "application/json"}
    issue_fields_without_issue_type: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary[:255],
        "description": issue_description_atlassian_document,
    }
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        for issue_type_name in ("Bug", "Task"):
            create_payload = {"fields": {**issue_fields_without_issue_type, "issuetype": {"name": issue_type_name}}}
            http_response = await http_client.post(create_issue_url, headers=request_headers, json=create_payload)
            if http_response.status_code == 400 and issue_type_name == "Bug":
                continue
            http_response.raise_for_status()
            return http_response.json()
    raise ValueError("Jira: could not create issue (Bug/Task). Check project permissions and issue types.")


async def _jira_attach_file(
    *,
    site_base_url: str,
    authorization_header: str,
    issue_key: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    url_encoded_issue_key = quote(issue_key, safe="")
    upload_url = f"{site_base_url.rstrip('/')}/rest/api/3/issue/{url_encoded_issue_key}/attachments"
    content_type = _video_mime_type(filename)
    async with httpx.AsyncClient(timeout=CLICKUP_HTTP_TIMEOUT) as http_client:
        http_response = await http_client.post(
            upload_url,
            headers={
                "Authorization": authorization_header,
                # REST API v3: use no-check for multipart attachment uploads (CSRF bypass).
                "X-Atlassian-Token": "no-check",
                "Accept": "application/json",
            },
            files={"file": (filename, file_bytes, content_type)},
        )
        if http_response.is_error:
            detail = (http_response.text or "")[:1200]
            print(f"[Jira] attachment HTTP {http_response.status_code}: {detail}", flush=True)
        http_response.raise_for_status()


def _github_issue_body_markdown(
    test_record: dict[str, Any],
    testcases: list[dict[str, Any]],
    *,
    github_video_embeds: dict[int, dict[str, str]] | None = None,
) -> str:
    """GitHub issue body: same markdown report as ClickUp; optional ``github_video_embeds`` for GIF links."""
    return _markdown_from_test(
        test_record, testcases, github_video_embeds=github_video_embeds
    )[:65535]


def _github_encode_repository_path_for_url(repository_path: str) -> str:
    return "/".join(quote(part, safe="") for part in repository_path.split("/"))


def _github_media_urls_from_put(
    repository_full_name: str,
    repo_path: str,
    content_meta: dict[str, Any] | None,
    put_response: dict[str, Any],
) -> tuple[str, str]:
    """Return (raw_url, blob_url) for a committed file."""
    html_url = ""
    download_url = ""
    if isinstance(content_meta, dict):
        html_url = str(content_meta.get("html_url") or "").strip()
        download_url = str(content_meta.get("download_url") or "").strip()
    commit_sha = ""
    if isinstance(put_response, dict):
        commit_obj = put_response.get("commit")
        if isinstance(commit_obj, dict):
            commit_sha = str(commit_obj.get("sha") or "").strip()
    try:
        owner, repo = repository_full_name.split("/", 1)
    except ValueError:
        owner, repo = "", ""
    encoded = _github_encode_repository_path_for_url(repo_path)
    raw_url = download_url
    if not raw_url and commit_sha and owner and repo:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/{encoded}"
    blob_url = html_url
    if not blob_url and commit_sha and owner and repo:
        blob_url = f"https://github.com/{owner}/{repo}/blob/{commit_sha}/{encoded}"
    return raw_url, blob_url


def _github_recording_markdown_block(
    seq: int,
    display_name: str,
    repo_file: str,
    repo_path: str,
    repository_full_name: str,
    content_meta: dict[str, Any] | None,
    put_response: dict[str, Any],
    *,
    poster_raw_url: str | None = None,
    poster_repo_path: str | None = None,
    gif_img_src: str | None = None,
    click_href: str | None = None,
) -> str:
    """Blob/raw links for video; preview GIF uses ``blob/...?raw=true``; click goes to ``click_href`` (recording URL)."""
    raw_url, blob_url = _github_media_urls_from_put(
        repository_full_name, repo_path, content_meta, put_response
    )

    lines = [f"### {seq}. {display_name}", f"- **Video in repo:** `{repo_path}`"]
    if poster_repo_path:
        lines.append(f"- **Preview GIF:** `{poster_repo_path}`")
    if blob_url:
        lines.append(f"- **Open on GitHub:** [{repo_file}]({blob_url})")
    if raw_url:
        lines.append(f"- **Raw video:** [`{repo_file}`]({_markdown_href(raw_url)})")
    if not blob_url and not raw_url:
        lines.append(f"- **File:** `{repo_file}`")

    body = "\n".join(lines)

    img_u = (gif_img_src or poster_raw_url or "").strip()
    link_u = (click_href or raw_url or "").strip()
    if poster_raw_url and raw_url and img_u and link_u:
        alt = (display_name or "Recording")[:200].replace("]", "").replace("[", "")
        body += "\n\n" + _github_clickable_preview_gif_html(img_u, link_u, alt) + "\n"
    elif raw_url:
        body += (
            "\n\n_No preview GIF (ffmpeg missing or extract failed); use **Raw video** above._\n"
        )

    return body


async def _github_get_default_branch(
    *, personal_access_token: str, repository_full_name: str
) -> str:
    repo_meta_url = f"{GITHUB_REST_API}/repos/{repository_full_name}"
    request_headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        http_response = await http_client.get(repo_meta_url, headers=request_headers)
        http_response.raise_for_status()
        default_branch = str(http_response.json().get("default_branch") or "").strip()
        return default_branch or "main"


async def _github_put_repository_file(
    *,
    personal_access_token: str,
    repository_full_name: str,
    repository_path: str,
    file_bytes: bytes,
    commit_message: str,
    branch: str,
) -> dict[str, Any]:
    encoded_path = _github_encode_repository_path_for_url(repository_path)
    put_url = f"{GITHUB_REST_API}/repos/{repository_full_name}/contents/{encoded_path}"
    request_headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request_body: dict[str, Any] = {
        "message": commit_message[:250],
        "content": base64.b64encode(file_bytes).decode("ascii"),
        "branch": branch,
    }
    async with httpx.AsyncClient(timeout=CLICKUP_HTTP_TIMEOUT) as http_client:
        http_response = await http_client.put(put_url, headers=request_headers, json=request_body)
        if http_response.is_error:
            detail = (http_response.text or "")[:1200]
            print(f"[GitHub] contents API HTTP {http_response.status_code}: {detail}", flush=True)
        http_response.raise_for_status()
        return http_response.json()


async def _github_post_issue_comment(
    *,
    personal_access_token: str,
    repository_full_name: str,
    issue_number: int,
    comment_body: str,
) -> dict[str, Any]:
    comment_url = f"{GITHUB_REST_API}/repos/{repository_full_name}/issues/{issue_number}/comments"
    request_headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        http_response = await http_client.post(
            comment_url,
            headers=request_headers,
            json={"body": comment_body[:65535]},
        )
        if http_response.is_error:
            detail = (http_response.text or "")[:1200]
            print(f"[GitHub] issue comment HTTP {http_response.status_code}: {detail}", flush=True)
        http_response.raise_for_status()
        return http_response.json()


async def _github_patch_issue_body(
    *,
    personal_access_token: str,
    repository_full_name: str,
    issue_number: int,
    new_body: str,
) -> None:
    patch_url = f"{GITHUB_REST_API}/repos/{repository_full_name}/issues/{issue_number}"
    request_headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        http_response = await http_client.patch(
            patch_url,
            headers=request_headers,
            json={"body": new_body[:65535]},
        )
        if http_response.is_error:
            detail = (http_response.text or "")[:1200]
            print(f"[GitHub] issue PATCH HTTP {http_response.status_code}: {detail}", flush=True)
        http_response.raise_for_status()


async def _github_create_issue(
    *,
    personal_access_token: str,
    repository_full_name: str,
    issue_title: str,
    issue_body_markdown: str,
) -> dict[str, Any]:
    create_issue_url = f"{GITHUB_REST_API}/repos/{repository_full_name}/issues"
    request_headers = {
        "Authorization": f"Bearer {personal_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_payload = {
        "title": issue_title[:256],
        "body": issue_body_markdown[:65535],
    }
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        for label_names in (["bug", "automated-report"], []):
            create_payload = {**base_payload, "labels": label_names}
            http_response = await http_client.post(
                create_issue_url,
                headers=request_headers,
                json=create_payload,
            )
            if http_response.status_code == 422 and label_names:
                continue
            http_response.raise_for_status()
            return http_response.json()
    raise ValueError("GitHub: could not create issue. Check token scopes (issues: write) and repository access.")


class PlatformIntergrate:
    @staticmethod
    async def clickup_export(
        *,
        user_id: int,
        test_record: dict[str, Any],
        testcases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        provider_row = get_provider_key(user_id, "clickup")
        if not provider_row:
            raise ValueError("No ClickUp integration for this user.")
        api_token = (provider_row.get("provider_api_key") or "").strip()
        if not api_token:
            raise ValueError("ClickUp token missing (save via PUT /user/providers).")

        provider_config_raw = provider_row.get("provider_config")
        if isinstance(provider_config_raw, str):
            provider_config = json.loads(provider_config_raw)
        elif isinstance(provider_config_raw, dict):
            provider_config = dict(provider_config_raw)
        else:
            provider_config = {}

        clickup_list_id = resolve_clickup_list_id(provider_config)
        if not clickup_list_id:
            raise ValueError("ClickUp list_id / list_url missing in provider_config.")

        report_markdown = _markdown_from_test(test_record, testcases)
        test_name = str(test_record.get("test_name") or "Test")

        create_task_url = f"{CLICKUP_API}/list/{clickup_list_id}/task"
        request_payload = {
            "name": f"[Bug Report] {test_name}"[:500],
            "markdown_description": report_markdown,
            "description": f"Test report: {test_name}"[:4000],
        }
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            http_response = await http_client.post(
                create_task_url,
                headers={"Authorization": api_token, "Content-Type": "application/json"},
                json=request_payload,
            )
            http_response.raise_for_status()
            created_task = http_response.json()

        clickup_task_id = str(created_task.get("id", ""))
        attachments_uploaded = 0

        for testcase in testcases:
            video_url = (testcase.get("testcase_video") or "").strip()
            if not video_url:
                continue
            testcase_name = testcase.get("testcase_name") or "testcase"
            filename = _attachment_filename(video_url, testcase_name)
            try:
                video_bytes = await _download_bytes_from_url(video_url)
                if not video_bytes:
                    print(
                        f"[ClickUp] video skipped (empty or too large): {video_url[:80]}…",
                        flush=True,
                    )
                    continue
                await _clickup_attach_file(
                    api_token=api_token,
                    clickup_task_id=clickup_task_id,
                    filename=filename,
                    file_bytes=video_bytes,
                )
                attachments_uploaded += 1
            except Exception as exc:
                print(f"[ClickUp] video attachment failed ({filename}): {exc}", flush=True)

        return {
            "success": True,
            "platform": "clickup",
            "task_id": clickup_task_id,
            "task_url": created_task.get("url"),
            "list_id": clickup_list_id,
            "attachments_uploaded": attachments_uploaded,
        }

    @staticmethod
    async def jira_export(
        *,
        user_id: int,
        test_record: dict[str, Any],
        testcases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        provider_row = get_provider_key(user_id, "jira")
        if not provider_row:
            raise ValueError("No Jira integration for this user.")
        api_token = (provider_row.get("provider_api_key") or "").strip()
        if not api_token:
            raise ValueError("Jira API token missing (save via PUT /user/providers).")

        provider_config_raw = provider_row.get("provider_config")
        if isinstance(provider_config_raw, str):
            provider_config = json.loads(provider_config_raw)
        elif isinstance(provider_config_raw, dict):
            provider_config = dict(provider_config_raw)
        else:
            provider_config = {}

        atlassian_email = str(provider_config.get("email") or "").strip()
        site_hostname = str(provider_config.get("site_hostname") or "").strip().lower()
        project_key = str(provider_config.get("project_key") or "").strip().upper()
        if not atlassian_email or not site_hostname or not project_key:
            raise ValueError("Jira: email, site, or project_key missing in saved provider_config.")

        site_base_url = f"https://{site_hostname}"
        authorization_header = _jira_basic_auth_header(atlassian_email, api_token)

        test_name = str(test_record.get("test_name") or "Test")
        summary = f"[Bug Report] {test_name}"[:255]
        issue_description_atlassian_document = _build_jira_issue_description_atlassian_document(
            test_record, testcases
        )

        created_issue_response = await _jira_create_issue(
            site_base_url=site_base_url,
            authorization_header=authorization_header,
            project_key=project_key,
            summary=summary,
            issue_description_atlassian_document=issue_description_atlassian_document,
        )
        issue_key = str(created_issue_response.get("key") or "")
        issue_id = str(created_issue_response.get("id") or "")
        issue_browser_base_url = f"{site_base_url}/browse"

        attachments_uploaded = 0
        for testcase in testcases:
            video_url = (testcase.get("testcase_video") or "").strip()
            if not video_url:
                continue
            testcase_name = testcase.get("testcase_name") or "testcase"
            filename = _attachment_filename(video_url, testcase_name)
            try:
                video_bytes = await _download_bytes_from_url(video_url)
                if not video_bytes:
                    print(
                        f"[Jira] video skipped (empty or too large): {video_url[:80]}…",
                        flush=True,
                    )
                    continue
                filename = _jira_normalize_video_filename(filename, video_bytes)
                await _jira_attach_file(
                    site_base_url=site_base_url,
                    authorization_header=authorization_header,
                    issue_key=issue_key,
                    filename=filename,
                    file_bytes=video_bytes,
                )
                attachments_uploaded += 1
            except Exception as exc:
                print(f"[Jira] video attachment failed ({filename}): {exc}", flush=True)

        return {
            "success": True,
            "platform": "jira",
            "issue_key": issue_key,
            "issue_id": issue_id,
            "issue_url": f"{issue_browser_base_url}/{issue_key}" if issue_key else None,
            "project_key": project_key,
            "attachments_uploaded": attachments_uploaded,
        }

    @staticmethod
    async def github_export(
        *,
        user_id: int,
        test_record: dict[str, Any],
        testcases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        provider_row = get_provider_key(user_id, "github")
        if not provider_row:
            raise ValueError("No GitHub integration for this user.")
        personal_access_token = (provider_row.get("provider_api_key") or "").strip()
        if not personal_access_token:
            raise ValueError("GitHub token missing (save via PUT /user/providers).")

        provider_config_raw = provider_row.get("provider_config")
        if isinstance(provider_config_raw, str):
            provider_config = json.loads(provider_config_raw)
        elif isinstance(provider_config_raw, dict):
            provider_config = dict(provider_config_raw)
        else:
            provider_config = {}

        repository_full_name = str(provider_config.get("repo") or "").strip()
        if not repository_full_name:
            raise ValueError("GitHub: repository (owner/name) missing in provider_config.")

        test_name = str(test_record.get("test_name") or "Test")
        issue_title = f"[Bug Report] {test_name}"[:256]
        issue_body_markdown = _github_issue_body_markdown(test_record, testcases)

        created_issue = await _github_create_issue(
            personal_access_token=personal_access_token,
            repository_full_name=repository_full_name,
            issue_title=issue_title,
            issue_body_markdown=issue_body_markdown,
        )

        issue_number = created_issue.get("number")
        issue_number_int = int(issue_number) if issue_number is not None else 0

        video_case_indices = [
            idx
            for idx, testcase in enumerate(testcases)
            if (testcase.get("testcase_video") or "").strip()
        ]
        attachments_uploaded = 0
        recording_lines: list[str] = []
        recordings_comment_url: str | None = None
        github_video_embeds: dict[int, dict[str, str]] = {}

        if video_case_indices and issue_number_int:
            try:
                default_branch = await _github_get_default_branch(
                    personal_access_token=personal_access_token,
                    repository_full_name=repository_full_name,
                )
            except Exception as exc:
                print(f"[GitHub] could not resolve default branch (skip repo uploads): {exc}", flush=True)
                default_branch = ""

            if default_branch:
                for seq, testcase_index in enumerate(video_case_indices, start=1):
                    testcase = testcases[testcase_index]
                    video_url = (testcase.get("testcase_video") or "").strip()
                    testcase_name = testcase.get("testcase_name") or "testcase"
                    filename = _attachment_filename(video_url, testcase_name)
                    try:
                        video_bytes = await _download_bytes_from_url(video_url)
                        if not video_bytes:
                            print(
                                f"[GitHub] video skipped (empty or too large): {video_url[:80]}…",
                                flush=True,
                            )
                            continue
                        upload_bytes, upload_base = await asyncio.to_thread(
                            _github_prepare_recording_file, filename, video_bytes
                        )
                        if len(upload_bytes) > GITHUB_MAX_REPO_FILE_BYTES:
                            print(
                                f"[GitHub] video skipped (over {GITHUB_MAX_REPO_FILE_BYTES} bytes): "
                                f"{upload_base}",
                                flush=True,
                            )
                            continue
                        stem = _github_safe_path_segment(os.path.splitext(upload_base)[0])
                        ext = os.path.splitext(upload_base)[1] or ".mp4"
                        repo_file = f"{seq:02d}_{stem}{ext}"
                        repo_path = f".github/test-evidence/{issue_number_int}/{repo_file}"
                        commit_message = (
                            f"test(export): screen recording for issue #{issue_number_int} ({repo_file})"
                        )
                        put_response = await _github_put_repository_file(
                            personal_access_token=personal_access_token,
                            repository_full_name=repository_full_name,
                            repository_path=repo_path,
                            file_bytes=upload_bytes,
                            commit_message=commit_message,
                            branch=default_branch,
                        )
                        content_meta = put_response.get("content") if isinstance(put_response, dict) else None
                        content_dict = content_meta if isinstance(content_meta, dict) else None
                        display_name = testcase_name if isinstance(testcase_name, str) else str(testcase_name)
                        put_dict = put_response if isinstance(put_response, dict) else {}
                        video_raw_url, _ = _github_media_urls_from_put(
                            repository_full_name,
                            repo_path,
                            content_dict,
                            put_dict,
                        )

                        gif_repo_path = f"{os.path.splitext(repo_path)[0]}.gif"
                        gif_file = os.path.basename(gif_repo_path)
                        gif_raw_url: str | None = None
                        gif_cdict: dict[str, Any] | None = None
                        gif_bytes = await asyncio.to_thread(
                            _ffmpeg_extract_preview_gif, upload_bytes, ext or ".mp4"
                        )
                        if gif_bytes and len(gif_bytes) <= GITHUB_MAX_REPO_FILE_BYTES:
                            try:
                                gif_put = await _github_put_repository_file(
                                    personal_access_token=personal_access_token,
                                    repository_full_name=repository_full_name,
                                    repository_path=gif_repo_path,
                                    file_bytes=gif_bytes,
                                    commit_message=(
                                        f"test(export): preview GIF for issue #{issue_number_int} ({gif_file})"
                                    ),
                                    branch=default_branch,
                                )
                                gif_content = (
                                    gif_put.get("content") if isinstance(gif_put, dict) else None
                                )
                                gif_cdict = gif_content if isinstance(gif_content, dict) else None
                                gif_put_dict = gif_put if isinstance(gif_put, dict) else {}
                                gr_url, _ = _github_media_urls_from_put(
                                    repository_full_name,
                                    gif_repo_path,
                                    gif_cdict,
                                    gif_put_dict,
                                )
                                if gr_url:
                                    gif_raw_url = gr_url
                            except Exception as gexc:
                                print(f"[GitHub] GIF upload failed ({gif_file}): {gexc}", flush=True)

                        src_video = video_url.strip()
                        if src_video.lower().startswith(("http://", "https://")):
                            click_target = src_video
                        else:
                            click_target = (
                                _github_click_href_from_test_record(test_record)
                                or video_raw_url
                                or ""
                            )
                        gif_src_disp: str | None = None
                        if gif_raw_url and video_raw_url:
                            gif_blob_page = (
                                str(gif_cdict.get("html_url") or "").strip() if gif_cdict else ""
                            )
                            gif_src_disp = _github_blob_raw_display_url(gif_blob_page)
                            if not gif_src_disp:
                                gif_src_disp = _github_blob_branch_raw_url(
                                    repository_full_name, default_branch, gif_repo_path
                                )
                            github_video_embeds[testcase_index] = {
                                "gif_src": gif_src_disp,
                                "click_url": click_target,
                                "gif_raw": gif_raw_url,
                                "video_raw": video_raw_url,
                            }

                        recording_lines.append(
                            _github_recording_markdown_block(
                                seq,
                                display_name,
                                repo_file,
                                repo_path,
                                repository_full_name,
                                content_dict,
                                put_dict,
                                poster_raw_url=gif_raw_url,
                                poster_repo_path=gif_repo_path if gif_raw_url else None,
                                gif_img_src=gif_src_disp,
                                click_href=click_target if gif_raw_url else None,
                            )
                        )
                        attachments_uploaded += 1
                    except Exception as exc:
                        print(f"[GitHub] repo video upload failed ({filename}): {exc}", flush=True)

                if recording_lines:
                    comment_body = (
                        "## Screen recordings (supplement)\n\n"
                        f"Same files as in the issue description: **`.github/test-evidence/{issue_number_int}/`**. "
                        "**GIF** ≈3s from **2s** into each recording; **click** opens the **original recording URL** "
                        "(falls back to test URL or repo raw link). "
                        "Links below mirror the **Test Video** sections above.\n\n"
                        + "\n\n".join(recording_lines)
                    )
                    try:
                        comment_response = await _github_post_issue_comment(
                            personal_access_token=personal_access_token,
                            repository_full_name=repository_full_name,
                            issue_number=issue_number_int,
                            comment_body=comment_body,
                        )
                        comment_url = str(comment_response.get("html_url") or "").strip()
                        if comment_url:
                            recordings_comment_url = comment_url
                    except Exception as exc:
                        print(f"[GitHub] failed to post recording comment: {exc}", flush=True)

                    final_body = _github_issue_body_markdown(
                        test_record,
                        testcases,
                        github_video_embeds=github_video_embeds or None,
                    )
                    try:
                        await _github_patch_issue_body(
                            personal_access_token=personal_access_token,
                            repository_full_name=repository_full_name,
                            issue_number=issue_number_int,
                            new_body=final_body,
                        )
                    except Exception as exc:
                        print(f"[GitHub] failed to patch issue body with GIFs + links: {exc}", flush=True)

        return {
            "success": True,
            "platform": "github",
            "issue_number": created_issue.get("number"),
            "issue_url": created_issue.get("html_url"),
            "repository": repository_full_name,
            "issue_node_id": created_issue.get("node_id"),
            "attachments_uploaded": attachments_uploaded,
            "recordings_comment_url": recordings_comment_url,
        }
