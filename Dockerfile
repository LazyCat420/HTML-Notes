# Tailwind stage: compile the utility classes the templates use into one static
# stylesheet. Tailwind used to run in the browser (cdn.tailwindcss.com); now it
# only exists here, at build time. tailwind.config.js scans app/ and adds the
# classes saved canvases still use (tailwind.safelist.json).
FROM node:26-slim AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tailwind.config.js tailwind.input.css tailwind.safelist.json ./
COPY app/ ./app/
RUN npm run build:css

FROM python:3.11-slim AS deps

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Runner stage
FROM python:3.11-slim AS runner
WORKDIR /app

# Create non-root user and group
RUN groupadd --system --gid 1001 appgrp \
    && useradd --system --uid 1001 --gid appgrp -m -d /home/appusr appusr

# Create necessary directories
RUN mkdir -p /app/data /app/logs && chown -R appusr:appgrp /app

# Copy Python venv
COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install curl and openssl for health check and HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends curl openssl && rm -rf /var/lib/apt/lists/*

# Copy app code
COPY app/ ./app/
COPY --from=css /build/app/static/tailwind.css ./app/static/tailwind.css

RUN chown -R appusr:appgrp /app

ENV PYTHONPATH="/app"
ENV HOST="0.0.0.0"
ENV PORT="8035"
ENV DATABASE_URL="/app/data/notes.db"

USER appusr

EXPOSE 8035

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8035/health/app || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8035"]
