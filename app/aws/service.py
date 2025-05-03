import boto3
import re
from typing import List, Tuple
from core.config import settings

# ==== AWS CONFIG (top-level constants) ====
AWS_REGION = settings.AWS_REGION
AWS_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = settings.AWS_SECRET_ACCESS_KEY
AWS_BUCKET = settings.AWS_S3_OBJECT_STORAGE
AWS_BASE_URL = f"https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com"


# Initialize boto3 client using config
s3 = boto3.client(
    's3',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

# ==== FUNCTIONS ====

def push(content: str, key: str):
    s3.put_object(Bucket=AWS_BUCKET, Key=key, Body=content.encode('utf-8'))
    url = f"{AWS_BASE_URL}/{key}"
    print(f"Uploaded {key} → {url}")
    return url

def parseCodeBlocks(raw_code: str) -> List[Tuple[str, str]]:
    code_pattern = re.compile(r'file="([^"]+)"\s*```[\w-]*\n([\s\S]*?)```')
    matches = code_pattern.findall(raw_code)
    return [(file_path.strip(), code.strip()) for file_path, code in matches]

def createCodeBase(raw_code: str, project_id: str):
    code_blocks = parseCodeBlocks(raw_code)
    for file_path, code in code_blocks:
        key = f"{project_id}/{file_path}"
        push(code, key)
    base_url = f"{AWS_BASE_URL}/{project_id}"
    return base_url
