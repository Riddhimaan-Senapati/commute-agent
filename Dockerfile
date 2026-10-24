# One container for the hosted demo: FastAPI (+ MCP subprocess) on 8000 inside the
# container, and Next.js on $PORT proxying /api/* to it. Only Next.js is public.

FROM node:20.19-bookworm-slim AS web
WORKDIR /web
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --omit=optional --no-audit --no-fund
COPY webapp/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM python:3.12.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
COPY --from=node:20.19-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY commute_agent/ commute_agent/
COPY web/ web/
COPY data/ data/
RUN uv sync --frozen --no-dev

# Runtime data (processed graphs, basemap, OTP cache) is a release asset, not in git.
ARG DATA_URL=https://github.com/Riddhimaan-Senapati/commute-agent/releases/download/data-v1/commute-data.tar.gz
RUN curl -fsSL "$DATA_URL" | tar -xz -C /app

COPY --from=web /web /app/webapp
COPY docker/render/start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV PORT=10000 COMMUTE_API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
EXPOSE 10000
CMD ["/app/start.sh"]
