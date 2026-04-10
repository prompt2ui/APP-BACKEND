import asyncio
import os
import traceback
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Body, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Literal, Any
import json

from .database import execute_query
from .services.case.graph import CaseAgentEntryPoint
from .services.script import TestScript
from .services.platform_export import PlatformIntergrate
from .services.script.node_summary import NodeSummary
from .services.provider_store import (
    list_providers_for_user,
    upsert_provider,
)
from .services.storage import StorageService
from .services.chat.graph import ask_about_testcase


def _serialize_for_sse(value: Any) -> Any:
    """Recursively coerce values so json.dumps never raises on odd nested types."""
    if isinstance(value, dict):
        return {str(k): _serialize_for_sse(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_for_sse(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


router = APIRouter()


class TestAttachmentIn(BaseModel):
    """Reference file bundled with testcase generation / draft (multiple allowed)."""

    model_config = ConfigDict(extra="ignore")
    file_name: str = "file"
    file_detail: str = ""
    mime_type: str = ""
    file_content_base64: str = ""


class TestDetailCaseGen(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_name: str = ""
    test_url: str = ""
    test_spec: str = ""
    test_attachments: List[TestAttachmentIn] = Field(default_factory=list)


class CreateTestcaseRequest(BaseModel):
    """Body for POST /agent/testcase — page extract + CaseBuilder LLM."""

    model_config = ConfigDict(extra="ignore")
    project_detail: Optional[Any] = None
    test_detail: TestDetailCaseGen


class TestDetailDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_name: str = ""
    test_url: str = ""
    test_spec: str = ""
    test_attachments: List[TestAttachmentIn] = Field(default_factory=list)


class TestcaseDraftRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current_testcase_name: str = ""
    current_testcase_description: str = ""
    test_detail: TestDetailDraft


@router.post("/health")
async def tets_health():
    return {
        "success":  True,
        "response": "Normal"
    }


@router.get("/projects")
async def get_projects():
    projects = execute_query("SELECT * FROM \"Project\" ORDER BY created_at DESC")
    return projects

@router.get("/project/{id}")
async def get_project_detail(id: int):
    # Get project
    project = execute_query(
        'SELECT * FROM "Project" WHERE project_id = %s',
        (id,),
        fetch="one"
    )

    # Get tests with testcases
    tests = execute_query(
        'SELECT * FROM "Test" WHERE project_id = %s ORDER BY start_time DESC',
        (id,),
    )

    # For each test, get its testcases + PDF summary history (newest first)
    for test in tests:
        testcases = execute_query(
            'SELECT * FROM "Testcase" WHERE test_id = %s ORDER BY created_at ASC',
            (test["test_id"],),
        )
        test["testcases"] = testcases
        summaries = execute_query(
            """SELECT id, test_id, test_summary, created_at
               FROM "Test_Summary"
               WHERE test_id = %s
               ORDER BY created_at DESC""",
            (test["test_id"],),
        )
        test["test_summaries"] = summaries or []

    project_data = {
        "project_id": project["project_id"],
        "user_id": project.get("user_id"),
        "project_name": project["project_name"],
        "project_thumbnail": project["project_thumbnail"],
        "project_description": project["project_description"],
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
    }

    return {
        "project": project_data,
        "tests": tests
    }

class CreateProjectRequest(BaseModel):
    user_id: int
    project_name: str
    project_thumbnail: Optional[str] = None
    project_description: Optional[str] = None


@router.post("/project")
async def create_projects(req: CreateProjectRequest):
    now = datetime.now()

    # 2. Insert the new project
    # Note: project_id is serial, so we omit it. 
    # created_at has a default, so we can omit it or pass 'now'.
    try:
        query = """
            INSERT INTO "Project" (
                user_id, 
                project_name, 
                project_thumbnail, 
                project_description, 
                updated_at
            ) 
            VALUES (%s, %s, %s, %s, %s)
            RETURNING project_id, created_at
        """
        
        params = (
            req.user_id,
            req.project_name,
            req.project_thumbnail,
            req.project_description,
            now
        )

        # Using "one" to get the RETURNING values
        new_record = execute_query(query, params, fetch="one")

        return {
            "success": True,
            "message": "Project created successfully",
            "data": {
                "project_id": new_record["project_id"],
                "project_name": req.project_name,
                "created_at": new_record["created_at"]
            }
        }

    except Exception as e:
        return {
            "success": False, 
            "error": str(e)
        }


class ChatMessagePayload(BaseModel):
    id: str
    project_id: str
    content: str
    sender: str
    attachments: Optional[List[str]] = None


class ProjectContext(BaseModel):
    project_id: int
    project_name: str
    project_description: Optional[str] = None
    project_code_path: Optional[str] = None
    project_figma_url: Optional[str] = None


class TestContext(BaseModel):
    test_id: int
    project_id: int
    test_name: str
    test_url: Optional[str] = None
    test_spec: Optional[str] = None


class TestcaseContext(BaseModel):
    testcase_id: int
    testcase_name: str
    testcase_description: Optional[str] = None
    testcase_result: Optional[str] = None
    testcase_script: Optional[str] = None
    testcase_video: Optional[str] = None
    testcase_type: Optional[str] = None
    testcase_status: Optional[str] = None
    testcase_category: Optional[str] = None
    testcase_piority: Optional[str] = None


class ChatContext(BaseModel):
    project: ProjectContext
    test: TestContext
    testcase: Optional[TestcaseContext] = None


class TestcaseChatRequest(BaseModel):
    message: ChatMessagePayload
    context: ChatContext

@router.delete("/test/{test_id}")
async def delete_test(test_id: int):
    test = execute_query(
        'SELECT * FROM "Test" WHERE test_id = %s',
        (test_id,),
        fetch="one"
    )
    if not test:
        return {"status": "error", "message": f"Test {test_id} not found"}

    storage = StorageService()
    await storage.delete_test_files(str(test["project_id"]), str(test_id))

    # Delete testcases first (foreign key), then test
    execute_query('DELETE FROM "Testcase" WHERE test_id = %s', (test_id,), fetch="none")
    execute_query('DELETE FROM "Test" WHERE test_id = %s', (test_id,), fetch="none")

    return {
        "success": True,
        "data": f"Deleted test {test_id} and all related files"
    }


@router.post("/chat/testcase")
async def chat_about_testcase(payload: TestcaseChatRequest):
    """
    เส้นทางสำหรับให้ผู้ใช้ถามคำถามเกี่ยวกับ Testcase ปัจจุบันของ project/test นั้น ๆ
    รับ payload จาก frontend ในรูปแบบเดียวกับตัวอย่าง:
    {
        "message": { ... },
        "context": { "project": ..., "test": ..., "testcase": ... }
    }
    """
    answer = await ask_about_testcase(
        message=payload.message.content,
        context=payload.context.model_dump(),
    )

    # ส่งกลับในรูปแบบ MessagesModel เดียวกับที่ frontend ใช้
    return {
        "id": str(uuid4()),
        "project_id": payload.message.project_id,
        "content": answer,
        "sender": "assistant",
        "attachments": [],
    }

@router.post("/agent/testcase")
async def create_testcase(req: CreateTestcaseRequest):
    """
    Generate testcases from `test_url` extraction + LLM.

    `test_detail.test_attachments`: optional list of user files (e.g. PNG, JPEG, WebP, PDF, TXT,
    CSV, DOCX, XLSX, XLS). DOCX and Excel are converted server-side to plain text for the LLM.
    Each item should include `file_name`, **`file_content_base64`** (raw base64, no data: prefix; if empty the file is skipped),
    `mime_type` when known, and **`file_detail`** — a short human note per file so the model
    knows how to use that attachment (spec excerpt, screen to cover, etc.). Multiple files allowed.

    On success, `data.testcase[]` includes `testcase_category` as returned by the model
    (intended vocabulary: `functional`, `visual`, `performance`, `error handling` — see Case Builder prompt).
    """
    try:
        results = await CaseAgentEntryPoint.case_generate(req.model_dump())
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Testcase generation failed",
                "error": str(e),
            },
        )

    try:
        with open("debug/output/testcase-generate.json", "w") as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

    page_extraction = results.get("page_extraction") if isinstance(results, dict) else None
    extraction_ok = bool(page_extraction.get("ok")) if isinstance(page_extraction, dict) else True
    extraction_errors = page_extraction.get("errors") if isinstance(page_extraction, dict) else []
    if extraction_errors is None:
        extraction_errors = []

    if not extraction_ok or (isinstance(extraction_errors, list) and len(extraction_errors) > 0):
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Extraction failed",
                "errors": extraction_errors,
                "data": results,
            },
        )

    return {
        "success": True,
        "data": results,
    }


@router.post("/agent/testcase/draft")
async def testcase_draft(req: TestcaseDraftRequest):
    """
    AI polish for one testcase row: improves `testcase_name` + `testcase_description`
    from the user's draft. Optional `test_detail.test_url` loads page context for grounded wording.
    Optional **`test_detail.test_attachments`** (same shape as `/agent/testcase`) adds images, PDF,
    TXT/CSV, DOCX, Excel, and per-file **`file_detail`** to the model. When the draft mixes distinct goals, `data` may
    include optional `suggested_second_testcase`.

    Body example:
    {
      "current_testcase_name": "...",
      "current_testcase_description": "...",
      "test_detail": {
        "test_name": "",
        "test_url": "",
        "test_spec": "",
        "test_attachments": [
          {"file_name": "req.pdf", "mime_type": "application/pdf", "file_detail": "AC from §3", "file_content_base64": "..."}
        ]
      }
    }
    """
    try:
        result = await CaseAgentEntryPoint.case_draft(req.model_dump())
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Testcase enhance failed",
                "error": str(e),
            },
        )

@router.post("/agent/testing/stream")
async def running_testcase_stream(request: Request, req: dict = Body(...)):
    """SSE stream for test script generation. Frontend may navigate to the detail page immediately
    and consume this stream there so the connection is not cancelled when leaving the create page.
    Generated responsive scripts: ~1000ms settle after each viewport resize (see Planner `responsive_testing_rule`)."""
    # Important:
    # - ScriptGenerateStream runs in a dedicated asyncio Task (see event_generator). It does not
    #   block the HTTP event loop for other routes (e.g. GET /project/:id) beyond normal SSE I/O.
    # - The worker must continue even if the client disconnects.
    # Unbounded queue so we never drop testcase_complete / done events when the client is slow.
    event_queue: asyncio.Queue = asyncio.Queue()

    async def run_in_background():
        try:
            await TestScript.ScriptGenerateStream(req, event_queue)
        except Exception as e:
            # Best-effort push; if no consumer, do not block the worker.
            try:
                event_queue.put_nowait({"type": "error", "message": str(e)})
            except asyncio.QueueFull:
                pass

    async def event_generator():
        asyncio.create_task(run_in_background())
        try:
            while True:
                # If the client disconnects, stop streaming but DO NOT cancel the worker.
                if await request.is_disconnected():
                    break
                event = await event_queue.get()
                safe_event = _serialize_for_sse(event)
                yield f"data: {json.dumps(safe_event, ensure_ascii=False, default=str)}\n\n"
                if event.get("type") in ("done", "error"):
                    break
        finally:
            # Do not cancel: continue running to completion and save results to Supabase.
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
class ProviderUpsertBody(BaseModel):
    user_id: int
    provider_type: str  # github | clickup | jira
    provider_api_key: Optional[str] = None
    provider_config: Optional[dict] = None


@router.get("/user/{user_id}/providers")
async def list_user_providers(user_id: int):
    """List saved integrations for a user (no raw API keys returned)."""
    rows = list_providers_for_user(user_id)
    return {"success": True, "data": rows}


@router.put("/user/providers")
async def save_user_provider(body: ProviderUpsertBody):
    """
    Save or update one provider (one row per provider_type per user).
    Secrets are stored server-side; clients should not send tokens on /agent/export.
    """
    try:
        row = upsert_provider(
            body.user_id,
            body.provider_type.strip().lower(),
            body.provider_api_key,
            body.provider_config,
        )
        return {
            "success": True,
            "data": {
                "provider_id": row.get("provider_id"),
                "provider_type": row.get("provider_type"),
                "provider_config": row.get("provider_config"),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExportTestBody(BaseModel):
    """Test context for export (aligned with summary). Extra keys are allowed."""

    model_config = ConfigDict(extra="allow")

    test_id: int
    project_id: int
    test_name: str
    test_url: Optional[str] = None
    project_name: Optional[str] = None


class ExportRequest(BaseModel):
    user_id: int
    destination: Literal["supabase", "github", "clickup", "jira", "pdf"]
    test: ExportTestBody
    testcases: list[dict[str, Any]]


def _ensure_test_owned_by_user(test_id: int, project_id: int, user_id: int) -> None:
    row = execute_query(
        """SELECT 1 AS ok
           FROM "Test" t
           INNER JOIN "Project" p ON p.project_id = t.project_id
           WHERE t.test_id = %s AND t.project_id = %s AND p.user_id = %s""",
        (test_id, project_id, user_id),
        fetch="one",
    )
    if not row:
        raise HTTPException(
            status_code=403,
            detail="Test not found or you do not have access to this project.",
        )


@router.post("/agent/export")
async def export_test_report(body: ExportRequest):
    # Frontend uses "supabase" for the in-app PDF + storage flow; same as "pdf" here.
    destination = "pdf" if body.destination == "supabase" else body.destination

    if destination == "pdf":
        _ensure_test_owned_by_user(
            body.test.test_id, body.test.project_id, body.user_id
        )
        summary_payload = {
            "test": body.test.model_dump(),
            "testcases": body.testcases,
            "export_destination": "pdf",
        }
        try:
            pdf_result = await NodeSummary.create_pdf_summary(summary_payload)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail=f"PDF generation failed: {e}",
            ) from e

        storage = StorageService()
        public_url = await storage.upload_summary_file(
            pdf_result["pdf_path"],
            pdf_result["filename"],
            str(body.test.project_id),
            str(body.test.test_id),
        )
        if not public_url:
            raise HTTPException(
                status_code=503,
                detail="PDF upload failed (storage disabled or Supabase error).",
            )

        inserted = execute_query(
            """INSERT INTO "Test_Summary" (test_id, test_summary)
               VALUES (%s, %s)
               RETURNING id""",
            (body.test.test_id, public_url),
            fetch="one",
        )
        return {
            "success": True,
            "destination": "pdf",
            "test_summary_id": inserted["id"],
            "test_summary_url": public_url,
        }

    if destination == "clickup":
        return await PlatformIntergrate.clickup_export(
            user_id=body.user_id,
            test_record=body.test.model_dump(),
            testcases=body.testcases,
        )
    if destination == "jira":
        return await PlatformIntergrate.jira_export(
            user_id=body.user_id,
            test_record=body.test.model_dump(),
            testcases=body.testcases,
        )
    if destination == "github":
        return await PlatformIntergrate.github_export(
            user_id=body.user_id,
            test_record=body.test.model_dump(),
            testcases=body.testcases,
        )
    raise HTTPException(
        status_code=501,
        detail=f"Export not implemented for destination: {destination}",
    )
