# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=24.18.1
ARG PYTHON_VERSION=3.12.11
ARG CODEX_VERSION=0.146.0

FROM node:${NODE_VERSION}-bookworm-slim AS codex-cli
ARG CODEX_VERSION
ARG NODE_VERSION
COPY --chmod=755 deploy/verify-codex-runtime.sh /usr/local/sbin/verify-codex-runtime
RUN npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}"
RUN /usr/local/sbin/verify-codex-runtime "${CODEX_VERSION}" "${NODE_VERSION}"

FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-builder
WORKDIR /build
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG CODEX_VERSION
ARG NODE_VERSION
ARG WORKBENCH_UID=1000
ARG WORKBENCH_GID=1000

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        git \
        libstdc++6 \
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
COPY --from=codex-cli /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=codex-cli /usr/local/sbin/verify-codex-runtime /usr/local/sbin/verify-codex-runtime
RUN <<'EOF'
set -eu
ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex
ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
EOF

COPY --from=backend-builder /wheels /wheels
RUN python -m pip install --no-cache-dir --root-user-action=ignore /wheels/*
RUN rm -rf /wheels
RUN /usr/local/sbin/verify-codex-runtime "${CODEX_VERSION}" "${NODE_VERSION}"

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
