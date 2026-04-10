# App backend

FastAPI backend with Postgres (via **Supabase**), Prisma schema sync, and optional exports (GitHub / ClickUp / Jira).

---

## Handoff: ติดตั้งโดยยังไม่มี Supabase เลย

ถ้ารับโปรเจกต์มาใหม่และยังไม่มีโปรเจกต์ Supabase ให้ทำตามลำดับนี้

### 1) สร้างโปรเจกต์ Supabase

1. ไปที่ [supabase.com](https://supabase.com) สร้างบัญชีและ **New project** (เลือก region / ตั้งรหัสผ่านฐานข้อมูลให้จำได้)
2. รอจนโปรเจกต์พร้อม

### 2) เก็บค่า API และ Database

ใน **Project Settings → API**:

- **Project URL** → `SUPABASE_PROJECT_URL`
- **anon public** → `SUPABASE_ANNON_KEY`
- **service_role** (เก็บเฉพาะบนเซิร์ฟเวอร์) → `SUPABASE_SERVICE_ROLE_KEY`  
- ถ้ามี **Publishable key** ใหม่ของโปรเจกต์คุณ → `SUPABASE_PUBLISHABLE_KEY` (ไม่บังคับถ้าใช้ anon อย่างเดียว)

ใน **Project Settings → Database → Connection string**:

- เลือก **URI** แบบที่ใช้กับ **connection pooling** (มักเป็น port **6543** และมี `?pgbouncer=true`) → `SUPABASE_DATABASE_URL`  
  ใช้สตริงที่ Supabase แสดงให้ แล้วแทนที่ `[YOUR-PASSWORD]` ด้วยรหัสผ่านฐานข้อมูลจริง
- เลือก **Direct** หรือ **Session** (มัก port **5432**, ไม่ใช้ pgbouncer สำหรับ migration) → `SUPABASE_DIRECT_URL`

> Prisma ใน repo นี้อ่าน `SUPABASE_DATABASE_URL` และ `SUPABASE_DIRECT_URL` จาก `src/prisma/schema.prisma`

### 3) Storage bucket

แอปใช้ Supabase Storage สำหรับอัปโหลดไฟล์:

1. ไปที่ **Storage → New bucket**
2. ตั้งชื่อให้ตรงกับ `OBJECTS_STORAGE_BUCKET` ใน `.env` (ค่าเริ่มต้นใน `.env.example` คือ `app-object-storage`)
3. ตั้งค่า policy ให้เหมาะกับการใช้งาน (เช่น service role บน backend — อย่าเปิดสิทธิ์สาธารณะเกินจำเป็น)

### 4) ไฟล์ environment

```bash
cp .env.example .env
```

แก้ `.env` ให้ครบทุกค่าที่ขีดเส้นใต้ไว้ใน `.env.example` โดยเฉพาะ `OPENAI_API_KEY` และค่า Supabase ทั้งหมด

**อย่า commit ไฟล์ `.env`** (มีใน `.gitignore` แล้ว)

### 5) ติดตั้งอัตโนมัติ (แนะนำ)

เมื่อ `.env` ครบแล้ว และเครื่องมี **[uv](https://github.com/astral-sh/uv)** กับ **Node.js (npm)** ให้รันครั้งเดียว:

```bash
chmod +x scripts/setup.sh   # ครั้งแรกเท่านั้น (ถ้ายังไม่ executable)
./scripts/setup.sh
```

สคริปต์จะทำให้: สร้าง `.venv` (ถ้ายังไม่มี) → `uv pip install -r requirements.txt` + `crawl4ai` → `npm install` → `prisma db push`  
ถ้าขาดตัวแปรใน `.env` หรือยังเป็นค่า placeholder (`YOUR_PROJECT_REF` ฯลฯ) สคริปต์จะหยุดและบอกชื่อตัวแปร

> **Windows:** ใช้ Git Bash / WSL หรือรันขั้นตอนใน §5 (manual) แทน

### 6) ติดตั้งแบบ manual (ทางเลือก)

```bash
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
uv pip install crawl4ai
npm install
uv run prisma db push --schema=src/prisma/schema.prisma
```

`db push` บนฐานว่างหรือ dev ที่ยอมรับการเปลี่ยน schema ได้ — ถ้ามีข้อมูลเดิม ให้อ่านหัวข้อ migration / enum ด้านล่าง

### 7) รันเซิร์ฟเวอร์

```bash
uv run uvicorn src.main:app --reload
```

---

## Prisma / migration notes

หลังเปลี่ยน enum หรือโมเดล `Provider` (เช่น `ProviderType`, unique `[user_id, provider_type]`) ให้รัน `db push` อีกครั้ง และ **สำรองข้อมูล** ถ้ามีแถว `Provider` เดิม

### อัปเกรดจาก `kind` / `ProviderKind` → `provider_type` / `ProviderType`

รัน **`scripts/sql/migrate_provider_add_kind.sql`** ใน Supabase **SQL Editor** (ฐานเดียวกับ `SUPABASE_DATABASE_URL`) หรือบนฐานว่างใช้ `uv run prisma db push --schema=src/prisma/schema.prisma`

### Error: `provider_type` / `kind` missing on `/user/.../providers`

ฐานยังเป็น schema เก่า:

1. รัน **`scripts/sql/migrate_provider_add_kind.sql`** ใน **Supabase → SQL Editor**
2. ลอง `GET /user/2/providers` อีกครั้ง

### `prisma db push` warns about Test/Testcase enum “data loss”

Prisma ต้องการ drop/recreate enum:

- **เก็บข้อมูล:** รัน SQL เฉพาะส่วนที่จำเป็น แล้วลอง `uv run prisma db pull --schema=src/prisma/schema.prisma` แล้ว merge เข้า `schema.prisma`
- **Dev / ยอมเสียข้อมูล:** `uv run prisma db push --schema=src/prisma/schema.prisma --accept-data-loss` (สำรองก่อนถ้าไม่แน่ใจ)

---

## Provider / export API

- `GET /user/{user_id}/providers` — รายการ integrations (`has_secret`, `provider_config` ไม่รวม token)
- `PUT /user/providers` — body `{ user_id, provider_type: "github"|"clickup"|"jira", provider_api_key?, provider_config? }`
- `POST /agent/export` — body `{ user_id, destination: "supabase"|"github"|"clickup"|"jira", test, testcases }` — credential โหลดจาก DB

`provider_config` ตัวอย่าง:

- **github:** `{ "repo": "owner/repository" }`
- **clickup:** `{ "list_url": "https://app.clickup.com/.../v/li/901816867059" }` หรือ `{ "list_id": "901816867059" }`
- **jira:** `{ "email": "you@company.com", "project_url": "https://your-site.atlassian.net/jira/software/projects/PROJ/list" }` — `provider_api_key` เป็น Jira API token

รายละเอียดพฤติกรรม ClickUp / Jira / GitHub (สร้าง task/issue, แนบไฟล์, Markdown) ดูใน `platform_export.py` และคอมเมนต์ในโค้ด

---

## ClickUp smoke check (dev only)

`POST /health/clickup-smoke` — ส่ง JSON body พร้อม token ของคุณ (หรือตั้ง `CLICKUP_SMOKE_TOKEN` + `CLICKUP_SMOKE_LIST_URL` ใน `.env` แล้วส่ง `{}`)

ตัวอย่างรูปแบบ body (แทนที่ด้วยค่าจริงของคุณ อย่า commit token):

```json
{
  "list_id": "YOUR_LIST_ID",
  "list_url": "https://app.clickup.com/.../v/li/YOUR_LIST_ID"
}
```

หรือสำหรับ Jira / GitHub ให้ส่งฟิลด์ตามที่ route ต้องการในโค้ด — **ห้ามใส่ token จริงใน README หรือ git**

ใน production ควรปิดหรือป้องกัน route นี้
