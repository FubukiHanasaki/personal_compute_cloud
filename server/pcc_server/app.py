import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException
from .config import get_settings
from .db import make_engine, session_factory


def create_app(settings=None):
    settings = settings or get_settings()
    app = FastAPI(title="Personal Compute Cloud", version="0.1.0")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessions = session_factory(app.state.engine)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": str(exc.status_code), "message": str(exc.detail),
                             "request_id": request.state.request_id}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "validation_error", "message": "请求字段或格式不合法",
                             "request_id": request.state.request_id}}, status_code=422)

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
        app.mount("/assets", StaticFiles(directory=settings.web_root / "assets"), name="assets")
    @app.get("/", include_in_schema=False)
    def portal():
        index = settings.web_root / "index.html"
        if not index.is_file():
            raise HTTPException(503, "请先构建 WebUI：npm run build --prefix web")
        return FileResponse(index)
    return app
