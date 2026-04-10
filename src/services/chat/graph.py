from typing import Any
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from src.config import env


class TestcaseChatResponse(BaseModel):
    answer: str = Field(
        description=(
            "คำตอบสำหรับผู้ใช้ โดยอิงจาก context ที่ได้รับเท่านั้น "
            "ต้องเป็นข้อความในรูปแบบ Markdown อย่างเป็นทางการ (ดูกติกาใน system prompt)"
        )
    )
    missing_data: list[str] = Field(
        default_factory=list,
        description="รายการข้อมูลที่ยังขาด หากข้อมูลไม่เพียงพอให้ตอบเป็นรายการสั้นๆ",
    )

SYSTEM_PROMPT = """
คุณคือ AI ผู้ช่วยวิเคราะห์ Testcase สำหรับระบบ Test Automation ของโปรเจกต์นี้
เชี่ยวชาญด้าน QA, Automated Testing, และการวิเคราะห์ผลการทดสอบ

รูปแบบคำตอบ (บังคับ):
- ตอบเป็นภาษาไทย โดยใช้ Markdown อย่างเป็นทางการ เพื่อให้ผู้ใช้อ่านและแยกโครงสร้างได้ชัดเจน
- ใช้หัวข้อ `##` หรือ `###` จัดกลุ่มประเด็นหลักและย่อย ห้ามใช้ `#` (หัวข้อระดับเดียว)
- ใช้ย่อหน้าและรายการ (`-` หรือลำดับเลข) ให้สอดคล้องกับเนื้อหา
- ใช้ **ตัวหนา** เน้นคำสำคัญ (เช่น ชื่อ field, สถานะผลทดสอบ, ข้อความสรุปสั้นๆ)
- ใช้ `inline code` สำหรับชื่อ selector, path, error message สั้นๆ, ค่า enum/config ที่เป็นอักษร
- ใช้บล็อกโค้ดสามขีดพร้อมระบุภาษา เมื่อต้องอ้างสคริปต์, stack trace, หรือ log หลายบรรทัด เช่น ```typescript ... ```
- ถ้ามีตารางเปรียบเทียบขั้นตอนหรือผลลัพธ์ ให้ใช้ตาราง Markdown (| หัวคอลัมน์ |) ได้
- หลีกเลี่ยงการส่งข้อความยาวที่เป็น plain text ต่อเนื่องโดยไม่มีหัวข้อหรือรายการ

หน้าที่หลัก:
ช่วยอธิบาย วิเคราะห์ และให้คำแนะนำเกี่ยวกับ Testcase โดยใช้ข้อมูลจาก context ที่ให้มา

กติกาสำคัญ:

1. ใช้ข้อมูลจาก context เท่านั้น
ห้ามสร้างข้อมูลใหม่ หรือสมมติรายละเอียดที่ไม่มีอยู่ใน context

2. หากข้อมูลไม่เพียงพอสำหรับการสรุป
ให้ระบุอย่างชัดเจนว่า
"จากข้อมูลที่มี ยังไม่สามารถสรุปได้แน่ชัด"
และระบุว่าควรมีข้อมูลอะไรเพิ่ม

3. ห้ามเดา root cause แบบเฉพาะเจาะจง
หากข้อมูลไม่พอ ให้เสนอเป็น "ความเป็นไปได้" เท่านั้น

4. การปฏิเสธคำถาม
- ถ้าเป็นการทักทายทั่วไปสั้น ๆ เช่น "hi", "สวัสดี", "หวัดดี" ให้ตอบกลับทักทายอย่างสุภาพ
- ด้วย pattern ต่อไปนี้ "สวัสดีครับ เราคือ AI ผู้ช่วยวิเคราะห์ Testcase สำหรับระบบ Test Automation ของโปรเจกต์นี้ สามารถถามเกี่ยวกับ Testcase ของโปรเจกต์นี้ได้"
- ถ้าผู้ใช้ถามเรื่องที่ชัดเจนว่าไม่เกี่ยวกับ project/test/testcase/automated testing
  เช่น การเงินส่วนตัว สุขภาพ ความรัก ฯลฯ ให้ปฏิเสธอย่างสุภาพและสั้น

ตัวอย่างคำตอบเมื่อปฏิเสธ:
"ขออภัย ระบบนี้ถูกออกแบบมาเพื่อช่วยวิเคราะห์ Testcase และการทดสอบของโปรเจกต์นี้เท่านั้น"

แนวทางการตอบ:

- ตอบเป็นภาษาไทย พร้อม Markdown ตามกติกาด้านบน
- สุภาพ กระชับ ชัดเจน
- ตอบตรงคำถามผู้ใช้ก่อนเสมอ
- หลีกเลี่ยง template ตายตัว

หากคำถามเกี่ยวกับ Testcase อาจวิเคราะห์ในมุมต่อไปนี้ (เลือกเฉพาะที่เกี่ยวข้อง):

• สรุปว่า Testcase ตรวจสอบอะไร
• พฤติกรรมระบบที่คาดหวัง
• วิเคราะห์ผลการทดสอบ (pass / fail)
• ปัจจัยที่อาจทำให้เกิดปัญหา
• แนวทางตรวจสอบหรือแก้ไข
• Testcase เพิ่มเติมที่ควรมี

หากข้อมูลไม่พอ ให้ระบุข้อมูลที่ควรเพิ่ม เช่น

- error log
- screenshot
- DOM selector / locator
- network response
- test script ล่าสุด
- step การทดสอบที่ใช้จริง

ก่อนตอบให้ตรวจสอบเสมอว่า:
คำตอบของคุณอิงข้อมูลจาก context จริง
และไม่ได้สร้างข้อมูลใหม่ขึ้นมาเอง
"""

llm = ChatOpenAI(
    api_key=env.OPENAI_API_KEY,
    model="gpt-4.1-mini",
    temperature=0.3,
    timeout=20,
    max_retries=2,
).with_structured_output(TestcaseChatResponse)



def _build_context(context: dict[str, Any]) -> str:
    project = context.get("project") or {}
    test = context.get("test") or {}
    testcase = context.get("testcase") or {}
    return f"""
ข้อมูลโครงการ (Project)
- ID: {project.get('project_id')}
- ชื่อโปรเจกต์: {project.get('project_name')}
- คำอธิบาย: {project.get('project_description')}
- โค้ดโปรเจกต์: {project.get('project_code_path')}
- ลิงก์ดีไซน์ / Figma: {project.get('project_figma_url')}

ข้อมูลการทดสอบ (Test)
- Test ID: {test.get('test_id')}
- Project ID (ของ Test): {test.get('project_id')}
- ชื่อ Test: {test.get('test_name')}
- URL ที่ใช้ทดสอบ: {test.get('test_url')}
- ข้อมูลสเปกการทดสอบ (test_spec): {test.get('test_spec')}

ข้อมูล Testcase ปัจจุบัน (ถ้ามี)
- Testcase ID: {testcase.get('testcase_id')}
- ชื่อ Testcase: {testcase.get('testcase_name')}
- คำอธิบาย Testcase: {testcase.get('testcase_description')}
- ผลการทดสอบ (result): {testcase.get('testcase_result')}
- สถานะ (status): {testcase.get('testcase_status')}
- Priority: {testcase.get('testcase_piority')}
- ประเภท: {testcase.get('testcase_type')}
- หมวดหมู่: {testcase.get('testcase_category')}
- วิดีโอหลักฐาน: {testcase.get('testcase_video')}

สคริปต์ที่ใช้รันทดสอบ (ถ้ามี):
{testcase.get('testcase_script') or '(ไม่มีข้อมูลสคริปต์ใน context นี้)'}
"""


async def ask_about_testcase(message: str, context: dict[str, Any]) -> str:
    project     = context.get("project") or {}
    test        = context.get("test") or {}
    testcase    = context.get("testcase") or {}
    context_text = f"""
ข้อมูลโครงการ (Project)
- ID: {project.get('project_id')}
- ชื่อโปรเจกต์: {project.get('project_name')}
- คำอธิบาย: {project.get('project_description')}
- โค้ดโปรเจกต์: {project.get('project_code_path')}
- ลิงก์ดีไซน์ / Figma: {project.get('project_figma_url')}

ข้อมูลการทดสอบ (Test)
- Test ID: {test.get('test_id')}
- Project ID (ของ Test): {test.get('project_id')}
- ชื่อ Test: {test.get('test_name')}
- URL ที่ใช้ทดสอบ: {test.get('test_url')}
- ข้อมูลสเปกการทดสอบ (test_spec): {test.get('test_spec')}

ข้อมูล Testcase ปัจจุบัน (ถ้ามี)
- Testcase ID: {testcase.get('testcase_id')}
- ชื่อ Testcase: {testcase.get('testcase_name')}
- คำอธิบาย Testcase: {testcase.get('testcase_description')}
- ผลการทดสอบ (result): {testcase.get('testcase_result')}
- สถานะ (status): {testcase.get('testcase_status')}
- Priority: {testcase.get('testcase_piority')}
- ประเภท: {testcase.get('testcase_type')}
- หมวดหมู่: {testcase.get('testcase_category')}
- วิดีโอหลักฐาน: {testcase.get('testcase_video')}

สคริปต์ที่ใช้รันทดสอบ (ถ้ามี):
{testcase.get('testcase_script') or '(ไม่มีข้อมูลสคริปต์ใน context นี้)'}
"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"ข้อมูล context:\n{context_text}\n\nคำถาม:\n{message}",
        },
    ]

    result: TestcaseChatResponse = await llm.ainvoke(messages)

    if result.missing_data:
        missing_text = "\n".join(f"- {item}" for item in result.missing_data)
        return (
            f"{result.answer}\n\n"
            f"### ข้อมูลที่แนะนำให้เพิ่ม\n\n{missing_text}"
        )

    return result.answer