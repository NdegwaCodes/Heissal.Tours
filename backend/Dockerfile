# backend/Dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# =========
# Builder image: install build deps + wheels
# =========
FROM base AS builder

WORKDIR /app

# System deps needed for building some wheels (e.g. cryptography / bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# =========
# Final runtime image
# =========
FROM base AS runtime

WORKDIR /app

# Create non-root user (optional but recommended)
RUN useradd -m appuser

# Copy wheels from builder and install
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Copy backend code
COPY backend/api ./backend/api

WORKDIR /app/backend/api

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
