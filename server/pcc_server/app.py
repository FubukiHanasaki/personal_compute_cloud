import uuid
import json
import logging
import html
import time
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException
from .config import get_settings
from .db import make_engine, session_factory, transaction
from .security import audit
from .middleware import BodyLimitMiddleware

log = logging.getLogger("pcc.http")


def create_app(settings=None):
    settings = settings or get_settings()
    app = FastAPI(
        title="Personal Compute Cloud", version="0.1.0", docs_url=None, redoc_url=None
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessions = session_factory(app.state.engine)
    from .storage import LocalStorage

    app.state.storage = LocalStorage(settings.storage_root)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = getattr(
            request.state, "request_id", uuid.uuid4().hex
        )
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed request_id=%s", request.state.request_id)
            response = JSONResponse(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "服务暂时不可用，请凭请求 ID 排查",
                        "request_id": request.state.request_id,
                    }
                },
                status_code=500,
            )
        if response.status_code in {401, 403, 413, 422}:
            try:
                with app.state.sessions() as db, transaction(db):
                    audit(
                        db,
                        getattr(request.state, "actor", "anonymous"),
                        "http.rejected",
                        request.url.path[:80],
                        str(response.status_code),
                    )
            except Exception:
                log.warning("audit_unavailable request_id=%s", request.state.request_id)
        log.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
        )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith(("/api/", "/internal/", "/v1/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": str(exc.status_code),
                    "message": str(exc.detail),
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "请求字段或格式不合法",
                    "request_id": request.state.request_id,
                }
            },
            status_code=422,
        )

    @app.get("/healthz")
    def health():
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": "0.1.0"}

    from .nodes import router as nodes_router

    app.include_router(nodes_router)
    from .templates import router as templates_router
    from .jobs import router as jobs_router

    app.include_router(templates_router)
    app.include_router(jobs_router)
    from .auth import router as auth_router

    app.include_router(auth_router)
    from .services import router as services_router
    from .ai import router as ai_router

    app.include_router(services_router)
    app.include_router(ai_router)
    if (settings.web_root / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=settings.web_root / "assets"),
            name="assets",
        )

    @app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
    def api_reference():
        rows = "".join(
            "<tr><td>"
            + html.escape(method.upper())
            + "</td><td><code>"
            + html.escape(path)
            + "</code></td><td>"
            + html.escape(operation.get("summary", ""))
            + "</td></tr>"
            for path, methods in app.openapi()["paths"].items()
            for method, operation in methods.items()
        )
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PCC API · Fubuki</title><style>body{font:14px system-ui;max-width:1000px;margin:40px auto;padding:20px;line-height:1.8}td{padding:8px 16px;border-bottom:1px solid #ddd}code{font-size:12px}</style><h1>PCC API</h1><p>作者：Fubuki · 完整请求/响应 Schema：<a href="/openapi.json">OpenAPI JSON</a>。程序使用 Bearer API Key；网页登录会话的写入请求需要 CSRF。</p><table>'
            + rows
            + "</table></html>"
        )

    @app.get("/", include_in_schema=False)
    def portal():
        index = settings.web_root / "index.html"
        if not index.is_file():
            raise HTTPException(503, "请先构建 WebUI：npm run build --prefix web")
        return FileResponse(index)

    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=((settings.artifact_max_bytes + 2) // 3) * 4 + 200000,
    )
    return app
