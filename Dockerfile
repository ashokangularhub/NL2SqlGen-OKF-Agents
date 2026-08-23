# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/
COPY okf_bundle/ ./okf_bundle/
COPY system_prompt.md .

# Copy SQLite database (seed data); mount a volume to persist writes
COPY retail_bank.db .

# Unprivileged user for security
RUN useradd --no-create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8081

# OPENAI_API_KEY must be supplied at runtime via --env or an env-file
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8081"]
