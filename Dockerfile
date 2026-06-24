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

# Install curl for health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy app code
COPY app/ ./app/

RUN chown -R appusr:appgrp /app

ENV PYTHONPATH="/app"
ENV HOST="0.0.0.0"
ENV PORT="8035"
ENV DATABASE_URL="/app/data/notes.db"

USER appusr

EXPOSE 8035

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8035/health/app || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8035"]
