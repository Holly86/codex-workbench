# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=24.18.1
ARG PYTHON_VERSION=3.12.11
ARG CODEX_VERSION=0.146.0

FROM node:${NODE_VERSION}-bookworm-slim AS codex-cli
ARG CODEX_VERSION
RUN npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}" \
    && version_output="$(codex --version 2>&1)" \
    && test "$version_output" = "codex-cli ${CODEX_VERSION}" \
    && codex app-server --help >/dev/null

FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-builder
WORKDIR /build
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG CODEX_VERSION
ARG WORKBENCH_UID=1026
ARG WORKBENCH_GID=100

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        git \
        make \
        openssh-client \
        procps \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && if ! getent group "${WORKBENCH_GID}" >/dev/null; then \
        groupadd --gid "${WORKBENCH_GID}" workbench; \
    fi \
    && useradd --uid "${WORKBENCH_UID}" --gid "${WORKBENCH_GID}" \
        --home-dir /home/workbench --create-home --shell /bin/bash workbench \
    && install -d -o "${WORKBENCH_UID}" -g "${WORKBENCH_GID}" \
        /app /var/lib/codex-workbench /var/lib/codex-workbench/state \
        /var/lib/codex-workbench/uploads /var/lib/codex-workbench/security \
        /home/workbench/.cache /home/workbench/.codex /home/workbench/.config \
        /home/workbench/.local /home/workbench/.npm /run/secrets/workbench

COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-cli /usr/local/bin/npm /usr/local/bin/npm
COPY --from=codex-cli /usr/local/bin/npx /usr/local/bin/npx
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex
COPY --from=codex-cli /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=backend-builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels \
    && version_output="$(/usr/local/bin/codex --version 2>&1)" \
    && test "$version_output" = "codex-cli ${CODEX_VERSION}" \
    && /usr/local/bin/codex app-server --help >/dev/null

WORKDIR /app
USER workbench
ENV HOME=/home/workbench \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKBENCH_ENVIRONMENT=production

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3)"]

CMD ["uvicorn", "codex_workbench.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
