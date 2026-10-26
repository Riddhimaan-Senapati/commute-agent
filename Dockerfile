# One container for the hosted demo on Hugging Face Spaces (free cpu-basic: 2 vCPU, 16 GB):
# OpenTripPlanner on 8080 and FastAPI (+ MCP subprocess) on 8000 inside the container, and
# Next.js on $PORT (7860) proxying /api/* to FastAPI. Only Next.js is public.

FROM node:20.19-bookworm-slim AS web
WORKDIR /web
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --omit=optional --no-audit --no-fund
COPY webapp/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

# OTP ships as a jib image: a Java runtime plus /app (jars and classpath files whose paths
# start with /app), so both are copied to the same places.
FROM opentripplanner/opentripplanner:2.10.0 AS otp

FROM python:3.12.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
COPY --from=node:20.19-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=otp /opt/java/openjdk /opt/java/openjdk
COPY --from=otp /app /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user
WORKDIR /home/user/app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --chown=user pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=user commute_agent/ commute_agent/
COPY --chown=user web/ web/
COPY --chown=user data/ data/
RUN uv sync --frozen --no-dev

# Runtime data and the OTP graph are release assets, not in git. The GTFS refresh workflow
# replaces the assets on the data-latest release; rebuilding the Space picks them up.
ARG DATA_RELEASE=https://github.com/Riddhimaan-Senapati/commute-agent/releases/download/data-latest
RUN curl -fsSL "$DATA_RELEASE/commute-data.tar.gz" | tar -xz -C /home/user/app \
    && mkdir -p data/otp \
    && curl -fsSL -o data/otp/graph.obj "$DATA_RELEASE/otp-graph.obj"

COPY --chown=user --from=web /web /home/user/app/webapp
COPY --chown=user docker/space/start.sh /home/user/app/start.sh
RUN chmod +x /home/user/app/start.sh

ENV PORT=7860 COMMUTE_API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1 \
    COMMUTE_OTP_URL=http://127.0.0.1:8080/otp/gtfs/v1 \
    OTP_JAVA_OPTS=-Xmx2g PATH=/opt/java/openjdk/bin:$PATH
EXPOSE 7860
CMD ["/home/user/app/start.sh"]
