# from pydantic import BaseModel
# from typing import List, Optional
# from datetime import datetime


# class ProjectSchema(BaseModel):
#     project_id: Optional[int] = None
#     project_name: str
#     project_thumbnail: Optional[str] = None
#     project_description: Optional[str] = None
#     project_code_path: Optional[str] = None
#     project_figma_url: Optional[str] = None
#     created_at: Optional[datetime] = None
#     updated_at: Optional[datetime] = None


# class TestSchema(BaseModel):
#     test_id: Optional[int] = None
#     project_id: Optional[int] = None
#     test_name: str
#     test_status: Optional[str] = "pending"
#     test_url: Optional[str] = None
#     test_source_reference: Optional[str] = None
#     test_documentation: Optional[str] = None
#     start_time: Optional[datetime] = None
#     end_time: Optional[datetime] = None


# class TestcaseSchema(BaseModel):
#     testcase_id: Optional[int] = None
#     test_id: Optional[int] = None
#     testcase_unique_id: Optional[str] = None
#     testcase_name: str
#     testcase_description: Optional[str] = None
#     testcase_script: Optional[str] = None
#     testcase_result: Optional[str] = None
#     testcase_type: Optional[str] = None
#     testcase_video: Optional[str] = None
#     testcase_status: Optional[str] = "running"
#     testcase_console_logs: Optional[str] = None
#     testcase_network_logs: Optional[str] = None
#     testcase_metadata: Optional[str] = None
#     testcase_category: Optional[str] = None
#     testcase_piority: Optional[str] = "low"
#     testcase_suggestion: Optional[str] = None
#     executed_at: Optional[datetime] = None


# class TestingRequest(BaseModel):
#     project: ProjectSchema
#     test: TestSchema
#     testcase_list: List[TestcaseSchema]
