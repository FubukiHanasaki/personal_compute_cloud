import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
    return app
