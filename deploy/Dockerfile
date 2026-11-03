FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
# The console imports the same palette that is packaged with the Agent GUI.
COPY agent/pcc_agent/gui_assets/theme.css /build/agent/pcc_agent/gui_assets/theme.css
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home pcc
WORKDIR /app
COPY pyproject.toml uv.lock setup.cfg LICENSE README.md ./
COPY server/ server/
COPY agent/ agent/
ENV UV_PROJECT_ENVIRONMENT=/app/tmp/venv UV_CACHE_DIR=/app/tmp/uv-cache
RUN mkdir -p tmp && pip install --no-cache-dir uv==0.13.0 && uv sync --frozen --no-dev --no-editable && rm -rf tmp/uv-cache
COPY alembic.ini ./
COPY migrations/ migrations/
COPY --from=web /build/tmp/web-dist /app/web-dist
RUN mkdir /data && chown pcc:pcc /data
USER 10001:10001
ENV PATH="/app/tmp/venv/bin:$PATH" PCC_STORAGE_ROOT=/data/artifacts PCC_WEB_ROOT=/app/web-dist
CMD ["sh", "-c", "alembic upgrade head && pcc-server"]
