from contextlib import asynccontextmanager
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from .routes    import router
from .config    import env
from .database  import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(title=env.APP_NAME, lifespan=lifespan)

# Mount static files for summaries
SUMMARY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "test/test-summary/output"))
os.makedirs(SUMMARY_PATH, exist_ok=True)
app.mount("/summaries", StaticFiles(directory=SUMMARY_PATH), name="summaries")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_and_timing_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or request.headers.get("x-correlation-id") or f"req-{uuid.uuid4().hex[:8]}"
    request.state.trace_id = trace_id

    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Let exception handlers format the response, but still allow correlation id.
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        request.state.duration_ms = duration_ms

    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Correlation-Id"] = trace_id
    response.headers["Server-Timing"] = f"app;dur={request.state.duration_ms:.1f}"
    return response


def _error_payload(*, code: str, message: str, trace_id: str, status_code: int, context: dict | None = None):
    return {
        "error": {
            "code": code,
            "message": message,
            "traceId": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "statusCode": status_code,
            "context": context or {"service": env.APP_NAME, "version": getattr(env, "APP_VERSION", "unknown")},
        }
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4().hex[:8]}")
    # If route already provided a structured detail, preserve it under context.
    detail = exc.detail
    msg = "Request failed"
    code = "HTTP_ERROR"
    ctx = {"service": env.APP_NAME, "version": getattr(env, "APP_VERSION", "unknown")}
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("error") or msg
        code = detail.get("code") or code
        ctx = {**ctx, **{k: v for k, v in detail.items() if k not in ("message", "error", "code")}}
    elif isinstance(detail, str):
        msg = detail

    payload = _error_payload(code=code, message=msg, trace_id=trace_id, status_code=exc.status_code, context=ctx)
    return JSONResponse(status_code=exc.status_code, content=payload, headers={"X-Trace-Id": trace_id, "X-Correlation-Id": trace_id})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4().hex[:8]}")
    payload = _error_payload(
        code="VALIDATION_ERROR",
        message="Invalid request payload",
        trace_id=trace_id,
        status_code=422,
        context={"service": env.APP_NAME, "version": getattr(env, "APP_VERSION", "unknown"), "errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=payload, headers={"X-Trace-Id": trace_id, "X-Correlation-Id": trace_id})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4().hex[:8]}")
    payload = _error_payload(
        code="INTERNAL_ERROR",
        message="Oh snap! A pesky bug has slipped through...",
        trace_id=trace_id,
        status_code=500,
        context={"service": env.APP_NAME, "version": getattr(env, "APP_VERSION", "unknown")},
    )
    return JSONResponse(status_code=500, content=payload, headers={"X-Trace-Id": trace_id, "X-Correlation-Id": trace_id})


app.include_router(router)
