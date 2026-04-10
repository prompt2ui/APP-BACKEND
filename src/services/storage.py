# src/services/storage.py
import asyncio
import json
import mimetypes
import os
import shutil
from typing import Any

import httpx

from src.config import env
from ..database import execute_query

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../test"))
TESTING_DIR = os.path.join(PROJECT_ROOT, "testing")
RESULT_DIR = os.path.join(PROJECT_ROOT, "test-result")
SUMMARY_DIR = os.path.join(PROJECT_ROOT, "test-summary")


class StorageService:
    def __init__(self):
        self.bucket = env.OBJECTS_STORAGE_BUCKET or "app-object-storage"
        self.base_path = (env.OBJECTS_STORAGE_BASE_PATH or "projects").strip("/")
        self.supabase_project_url = (env.SUPABASE_PROJECT_URL or "").rstrip("/")
        self.supabase_key = (
            env.SUPABASE_SERVICE_ROLE_KEY
            or env.SUPABASE_ANNON_KEY
            or env.SUPABASE_PUBLISHABLE_KEY
            or ""
        )
        self.provider = "disabled"

        if env.OBJECTS_STORAGE_ENDPOINT:
            from minio import Minio

            self.client = Minio(
                env.OBJECTS_STORAGE_ENDPOINT,
                access_key=env.OBJECTS_STORAGE_ROOT_USER,
                secret_key=env.OBJECTS_STORAGE_ROOT_PASSWORD,
                secure=False,
            )
            self.provider = "s3"
        else:
            self.client = None
            if self.supabase_project_url and self.supabase_key:
                self.provider = "supabase"

    def _build_object_path(self, *parts: Any) -> str:
        return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))

    def _build_public_url(self, object_path: str) -> str:
        object_path = object_path.lstrip("/")
        if self.provider == "supabase":
            return f"{self.supabase_project_url}/storage/v1/object/public/{self.bucket}/{object_path}"

        protocol = "https" if "supabase.co" in (env.OBJECTS_STORAGE_ENDPOINT or "") else "http"
        return f"{protocol}://{env.OBJECTS_STORAGE_ENDPOINT}/{self.bucket}/{object_path}"

    async def _upload_file(
        self,
        *,
        local_path: str,
        object_path: str,
        content_type: str | None = None,
    ) -> str | None:
        if not os.path.exists(local_path):
            return None

        if self.provider == "disabled":
            print(f"[StorageService] Storage upload skipped: provider is disabled for {local_path}")
            return None

        content_type = content_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        object_path = object_path.lstrip("/")

        if self.provider == "s3" and self.client:
            def _upload_to_s3() -> str:
                self.client.fput_object(
                    self.bucket,
                    object_path,
                    local_path,
                    content_type=content_type,
                )
                return self._build_public_url(object_path)

            return await asyncio.to_thread(_upload_to_s3)

        if self.provider == "supabase":
            upload_url = f"{self.supabase_project_url}/storage/v1/object/{self.bucket}/{object_path}"
            with open(local_path, "rb") as file:
                file_bytes = file.read()

            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key,
                "x-upsert": "true",
                "Content-Type": content_type,
            }

            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(upload_url, headers=headers, content=file_bytes)
                if response.status_code not in {200, 201}:
                    raise RuntimeError(
                        f"Supabase upload failed status={response.status_code} body={response.text[:400]}"
                    )

            return self._build_public_url(object_path)

        return None

    async def upload_summary_file(self, pdf_path: str, filename: str, project_id: str, test_id: str):
        """Upload summary PDF and return a public URL."""
        if not os.path.exists(pdf_path):
            return None

        try:
            # Match the new storage policy: summary/<file>.pdf
            object_path = self._build_object_path("summary", filename)
            
            return await self._upload_file(
                local_path=pdf_path,
                object_path=object_path,
                content_type="application/pdf",
            )
        except Exception as error:
            print(f"[StorageService] Summary upload failed: {error}")
            return None
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as rm_err:
                    print(f"[StorageService] Failed to delete temp file: {rm_err}")

    async def upload_testcase_video(self, project_id: str, test_id: str, testcase_unique_id: str):
        """Upload a testcase video and return a public URL."""
        if not testcase_unique_id:
            return None

        video_path = os.path.join(RESULT_DIR, testcase_unique_id, "video.webm")
        if not os.path.exists(video_path):
            return None

        try:
            return await self._upload_file(
                local_path=video_path,
                object_path=self._build_object_path(
                    self.base_path,
                    project_id,
                    test_id,
                    testcase_unique_id,
                    "video.webm",
                ),
                content_type="video/webm",
            )
        except Exception as error:
            print(f"[StorageService] Upload failed for {testcase_unique_id}: {error}")
            return None

    async def upload_testcase_artifacts(
        self,
        project_id: str,
        test_id: str,
        testcase_unique_id: str,
    ) -> dict[str, Any]:
        """Upload testcase artifacts and return a manifest of remote URLs."""
        manifest = {
            "provider": self.provider,
            "prefix": self._build_object_path(self.base_path, project_id, test_id, testcase_unique_id),
            "video_url": None,
            "files": {},
        }
        if not testcase_unique_id:
            return manifest

        testcase_dir = os.path.join(RESULT_DIR, testcase_unique_id)
        if not os.path.isdir(testcase_dir):
            return manifest

        for root, _, files in os.walk(testcase_dir):
            for name in sorted(files):
                local_path = os.path.join(root, name)
                relative_path = os.path.relpath(local_path, testcase_dir).replace(os.sep, "/")
                object_path = self._build_object_path(
                    self.base_path,
                    project_id,
                    test_id,
                    testcase_unique_id,
                    relative_path,
                )
                try:
                    public_url = await self._upload_file(local_path=local_path, object_path=object_path)
                except Exception as error:
                    print(f"[StorageService] Artifact upload failed for {relative_path}: {error}")
                    continue

                if not public_url:
                    continue

                manifest["files"][relative_path] = public_url
                # Playwright may place the video at the root or nested under
                # a subfolder. Treat any */video.webm as the testcase video.
                if relative_path == "video.webm" or relative_path.endswith("/video.webm"):
                    manifest["video_url"] = public_url

        # Fallback: if video_url wasn't set but a video exists in files, use it.
        if not manifest.get("video_url"):
            for rel_path, url in (manifest.get("files") or {}).items():
                if rel_path == "video.webm" or str(rel_path).endswith("/video.webm"):
                    manifest["video_url"] = url
                    break

        return manifest

    async def delete_test_files(self, project_id: str, test_id: str):
        """Delete all files under {BASE_PATH}/{project_id}/{test_id}/ in storage."""
        if self.provider != "s3" or not self.client:
            print("[StorageService] Remote delete is only supported for the S3-compatible storage provider.")
            return False

        try:
            def _delete():
                prefix = self._build_object_path(self.base_path, project_id, test_id) + "/"
                objects = self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
                for obj in objects:
                    self.client.remove_object(self.bucket, obj.object_name)
                    print(f"[StorageService] Deleted: {obj.object_name}")

            await asyncio.to_thread(_delete)
            print(f"[StorageService] Deleted all objects for test_id {test_id}")
            return True
        except Exception as error:
            print(f"[StorageService] Delete failed for test_id {test_id}: {error}")
            return False

    @staticmethod
    def cleanup(testcase_id: str):
        """ลบไฟล์ทั้งหมดของ testcase ที่รันเสร็จ"""
        if not testcase_id:
            return False

        testcase_file = os.path.join(TESTING_DIR, f"{testcase_id}.js")
        testcase_folder = os.path.join(RESULT_DIR, testcase_id)

        try:
            if os.path.exists(testcase_file):
                os.remove(testcase_file)

            if os.path.exists(testcase_folder):
                shutil.rmtree(testcase_folder)

            print(f"[Cleanup] Removed files for {testcase_id}")
            return True
        except Exception as error:
            print(f"[Cleanup] Error removing {testcase_id}: {error}")
            return False


class DatabaseService:
    async def get_test(self, test_id: int):
        test = execute_query(
            'SELECT * FROM "Test" WHERE test_id = %s',
            (test_id,),
            fetch="one"
        )
        if not test:
            return None

        testcases = execute_query(
            'SELECT * FROM "Testcase" WHERE test_id = %s ORDER BY created_at ASC',
            (test_id,),
        )
        test["testcases"] = testcases
        return test

    async def create_test(self, project_id: int, test_detail: dict):
        result = execute_query(
            '''INSERT INTO "Test" (project_id, test_name, test_status, test_url, test_source_reference, test_documentation, start_time)
               VALUES (%s, %s, %s, %s, %s, %s, NOW())
               RETURNING test_id''',
            (
                project_id,
                test_detail.get("test_name", "Untitled Test"),
                "running",
                test_detail.get("test_url"),
                test_detail.get("test_spec"),
                test_detail.get("test_extraction"),
            ),
            fetch="one"
        )
        return result["test_id"]

    async def update_test_completed(self, test_id: int) -> None:
        """Mark parent Test row finished (TestcaseStatus.completed + end_time)."""
        execute_query(
            '''UPDATE "Test"
               SET test_status = %s,
                   end_time = NOW()
               WHERE test_id = %s''',
            ("completed", test_id),
            fetch="none",
        )

    async def get_testcase(self, testcase_id: int):
        return execute_query(
            'SELECT * FROM "Testcase" WHERE testcase_id = %s',
            (testcase_id,),
            fetch="one"
        )

    async def create_testcase(self, test_id: int, testcase_detail: dict) -> int:
        # Normalize priority to enum values: low | medium | high
        raw_priority = (testcase_detail.get("testcase_priority") or "low").lower()
        if raw_priority not in {"low", "medium", "high"}:
            raw_priority = "low"
        # Schema uses: TestcaseResult { fail, success } and TestcaseStatus { running, completed }.
        # - When we create the row initially, we don't know final result yet -> status=running, result=fail (default).
        # - If caller provides test_validation, we can set completed immediately (fallback mode).
        raw_validation = (testcase_detail.get("test_validation") or "").strip().lower()
        if raw_validation in {"pass", "success"}:
            testcase_result = "success"
            status = "completed"
        elif raw_validation in {"fail", "failure"}:
            testcase_result = "fail"
            status = "completed"
        else:
            testcase_result = "fail"
            status = "running"

        tc_payload = testcase_detail.get("testcase") or {}
        if not isinstance(tc_payload, dict):
            tc_payload = {}
        raw_category = str(tc_payload.get("testcase_category") or "").strip() or None

        result = execute_query(
            '''INSERT INTO "Testcase" (test_id, testcase_unique_id, testcase_video, testcase_name, testcase_description, testcase_script, testcase_result, testcase_category, testcase_piority, testcase_status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
               RETURNING testcase_id''',
            (
                test_id,
                testcase_detail.get("testcase_unique_id"),
                None,
                tc_payload.get("testcase_name"),
                tc_payload.get("testcase_description"),
                testcase_detail.get("test_script", ""),
                testcase_result,
                raw_category,
                raw_priority,
                status,
            ),
            fetch="one"
        )
        return result["testcase_id"]

    async def update_testcase_logs(
        self,
        testcase_id: int,
        testcase_unique_id: str,
        uploaded_artifacts: dict[str, Any] | None = None,
        testcase_script: str | None = None,
        test_validation: str | None = None,
        testcase_suggestion: str | None = None,
    ):
        testcase_folder = os.path.join(RESULT_DIR, testcase_unique_id)
        console_log_path = os.path.join(testcase_folder, "console-logs.json")
        network_log_path = os.path.join(testcase_folder, "network-logs.json")
        metadata_path = os.path.join(testcase_folder, "metadata.json")

        testcase = execute_query(
            'SELECT * FROM "Testcase" WHERE testcase_id = %s',
            (testcase_id,),
            fetch="one"
        )
        if not testcase:
            print(f"[DatabaseService] Testcase {testcase_id} not found")
            return False

        def read_json_safe(path):
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as file:
                    return json.load(file)
            return None

        console_logs = read_json_safe(console_log_path)
        network_logs = read_json_safe(network_log_path)
        metadata = read_json_safe(metadata_path)

        metadata_payload: dict[str, Any] = {}
        if isinstance(metadata, dict):
            metadata_payload.update(metadata)
        elif metadata is not None:
            metadata_payload["raw_metadata"] = metadata

        uploaded_artifacts = uploaded_artifacts or {}
        remote_files = uploaded_artifacts.get("files") or {}
        if remote_files:
            metadata_payload["remote_artifacts"] = remote_files
            metadata_payload["storage_provider"] = uploaded_artifacts.get("provider")
            metadata_payload["storage_prefix"] = uploaded_artifacts.get("prefix")

        # Prefer explicit video_url; otherwise try to find it in remote files.
        video_url = uploaded_artifacts.get("video_url")
        if not video_url and isinstance(remote_files, dict):
            video_url = remote_files.get("video.webm")
            if not video_url:
                for rel_path, url in remote_files.items():
                    if str(rel_path).endswith("/video.webm"):
                        video_url = url
                        break

        # Map internal test_validation (pass/fail) -> DB enum (success/fail)
        raw_validation = (test_validation or "").strip().lower()
        if raw_validation in {"pass", "success"}:
            testcase_result = "success"
        else:
            # Default to fail when validation isn't provided or indicates failure
            testcase_result = "fail"
        testcase_status = "completed"

        suggestion = testcase_suggestion
        if suggestion is None:
            suggestion = testcase.get("testcase_suggestion")
        if suggestion is not None:
            suggestion = str(suggestion).strip() or None

        execute_query(
            '''UPDATE "Testcase"
               SET testcase_video = %s,
                   testcase_console_logs = %s,
                   testcase_network_logs = %s,
                   testcase_metadata = %s,
                   testcase_script = COALESCE(%s, testcase_script),
                   testcase_result = %s,
                   testcase_status = %s,
                   testcase_suggestion = COALESCE(%s, testcase_suggestion)
               WHERE testcase_id = %s''',
            (
                video_url or testcase.get("testcase_video"),
                json.dumps(console_logs, ensure_ascii=False) if console_logs else None,
                json.dumps(network_logs, ensure_ascii=False) if network_logs else None,
                json.dumps(metadata_payload, ensure_ascii=False) if metadata_payload else None,
                testcase_script,
                testcase_result,
                testcase_status,
                suggestion,
                testcase_id,
            ),
            fetch="none"
        )

        print(f"[DatabaseService] Updated logs for testcase {testcase_id}")
        return True
