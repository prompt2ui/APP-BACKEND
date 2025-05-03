# APP-BACKEND

Backend ของแอปพลิเคชันนี้พัฒนาด้วย Python และ FastAPI

## ข้อกำหนดเบื้องต้น

* **Python 3.8+** ติดตั้งบนเครื่องของคุณ (แนะนำให้ใช้เวอร์ชันล่าสุดที่เสถียร)
* **pip** (Python package installer) ซึ่งมักจะมาพร้อมกับการติดตั้ง Python
* **FastAPI** ติดตั้งอยู่ใน Virtual Environment ของคุณ (จะถูกติดตั้งเมื่อคุณรัน `pip install -r requirements.txt`)

## การติดตั้งและการใช้งาน

### 1. สร้าง Virtual Environment (venv)

เราแนะนำให้สร้าง Virtual Environment เพื่อจัดการ Dependencies ของโปรเจกต์นี้ แยกจาก Environment Python หลักของคุณ ทำได้โดยใช้คำสั่ง:

```bash
python -m venv venv
```

### 2. เปิดใช้งาน Virtual Environment

หลังจากสร้าง Virtual Environment แล้ว คุณต้องเปิดใช้งานมันก่อนที่จะติดตั้ง Dependencies

* **บน macOS และ Linux:**

    ```bash
    source venv/bin/activate
    ```

* **บน Windows:**

    ```bash
    venv\Scripts\activate
    ```

    เมื่อ Virtual Environment ถูกเปิดใช้งาน คุณจะเห็น `(venv)` นำหน้าชื่อ Terminal ของคุณ

### 3. ติดตั้ง Dependencies

โปรเจกต์นี้ใช้ Libraries หลายตัวที่ระบุไว้ในไฟล์ `requirements.txt` คุณสามารถติดตั้ง Dependencies ทั้งหมดได้โดยใช้ pip:

```bash
pip install -r requirements.txt
```

### 4. การรัน Development Server ด้วย `fastapi dev`

สำหรับ Development นั้น คุณสามารถรัน Server ได้อย่างง่ายดายด้วยคำสั่ง `fastapi dev`:

```bash
fastapi dev
```

คำสั่งนี้จะ:

* ค้นหา Application Instance (`app`) ในไฟล์หลักของโปรเจกต์คุณ (โดยทั่วไปคือ `main.py`)
* เริ่ม Uvicorn Server โดยอัตโนมัติ
* เปิดใช้งาน Auto-Reloading ทำให้ Server รีสตาร์ทเมื่อคุณมีการเปลี่ยนแปลงโค้ด

หลังจากรันคำสั่งนี้ Development Server ควรจะเริ่มทำงานและคุณสามารถเข้าถึง API endpoints ของคุณได้ (โดยทั่วไปจะอยู่ที่ `http://127.0.0.1:8000`). คุณยังสามารถเข้าถึง Documentation อัตโนมัติได้ที่ `http://127.0.0.1:8000/docs`.

## ข้อมูลเพิ่มเติม

* โปรดตรวจสอบไฟล์ `requirements.txt` สำหรับรายการ Dependencies ทั้งหมดที่โปรเจกต์นี้ใช้งาน
* สำหรับการรันใน Production Environment คุณอาจจะต้องใช้คำสั่ง `fastapi run` หรือปรับแต่งการตั้งค่า Server เพิ่มเติม โปรดดู Documentation ของ FastAPI สำหรับข้อมูลเพิ่มเติม

```