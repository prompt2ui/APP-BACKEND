import os
from langchain_openai import ChatOpenAI
from src.config import env

OPENAI_API_KEY = env.OPENAI_API_KEY
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEST_ROOT = os.path.join(PROJECT_ROOT, "src", "test")
TESTING_DIR = os.path.join(TEST_ROOT, "testing")
RESULT_DIR = os.path.join(TEST_ROOT, "test-result")
SUMMARY_DIR = os.path.join(TEST_ROOT, "test-summary")

llm = ChatOpenAI(
    api_key=OPENAI_API_KEY,
    # model="gpt-5.4-mini",
    model="gpt-4.1-mini"
)

llm_vision = ChatOpenAI(
    api_key=OPENAI_API_KEY,
    # model="gpt-5.4-mini",
    model="gpt-4.1-mini"
)

llm_extraction = ChatOpenAI(
    api_key=OPENAI_API_KEY,
    model="gpt-4.1-mini",
)

# llm_coding = ChatOpenAI(
#     api_key=OPENAI_API_KEY,
#     model="gpt-4.1-mini",
# )
