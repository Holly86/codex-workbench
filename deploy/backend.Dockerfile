# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=24.18.1
ARG PYTHON_VERSION=3.12.11
ARG CODEX_VERSION=0.146.0

FROM node:${NODE_VERSION}-bookworm-slim AS codex-cli
ARG CODEX_VERSION
RUN npm install --global --omit=dev @openai/codex@${CODEX_VERSION} \
    && codex --version

FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-builder
WORKDIR /build
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG WORKBENCH_UID=10001
ARG WORKBENCH_GID=10001

RUN groupadd --gid ${WORKBENCH_GID} workbench \
    && useradd --uid ${WORKBENCH_UID} --gid ${WORKBENCH_GID} \
        --home-dir /home/workbench --create-home --shell /usr/sbin/nologin workbench \
    && install -d -o workbench -g workbench \
        /app /var/lib/codex-workbench /var/lib/codex-workbench/uploads /home/workbench/.codex

COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex
COPY --from=backend-builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

WORKDIR /app
USER workbench:workbench
ENV HOME=/home/workbench \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKBENCH_ENVIRONMENT=production

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3)"]

CMD ["uvicorn", "codex_workbench.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
