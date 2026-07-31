# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=24.18.1

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28.0-alpine AS runtime
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /build/dist /usr/share/nginx/html

USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "-", "http://127.0.0.1:8080/healthz"]
