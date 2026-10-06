# syntax=docker/dockerfile:1

# ── Frontend build ───────────────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm --prefix frontend ci
COPY api/ api/
COPY frontend/ frontend/
# Vite inlines VITE_* at build time. Values come from Terraform outputs (CI vars).
ARG VITE_COGNITO_AUTHORITY
ARG VITE_COGNITO_CLIENT_ID
ARG VITE_COGNITO_DOMAIN
ENV VITE_COGNITO_AUTHORITY=$VITE_COGNITO_AUTHORITY \
    VITE_COGNITO_CLIENT_ID=$VITE_COGNITO_CLIENT_ID \
    VITE_COGNITO_DOMAIN=$VITE_COGNITO_DOMAIN
RUN npm --prefix frontend run build

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app esl
COPY pyproject.toml alembic.ini ./
COPY alembic/ alembic/
COPY src/ src/
RUN pip install .
COPY --from=frontend /src/frontend/dist frontend/dist
USER esl
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "esl.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
