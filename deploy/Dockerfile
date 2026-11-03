FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home pcc
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY server/ server/
COPY agent/ agent/
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations/ migrations/
COPY --from=web /build/tmp/web-dist /app/web-dist
RUN mkdir /data && chown pcc:pcc /data
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" PCC_STORAGE_ROOT=/data/artifacts PCC_WEB_ROOT=/app/web-dist
CMD ["sh", "-c", "alembic upgrade head && pcc-server"]
