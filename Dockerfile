# ══════════════════════════════════════════════════════════════════════════════
# Multi-stage Dockerfile
# Stage 1: Build frontend
# Stage 2: Build Python dependencies
# Stage 3: Lean runtime image with Supervisor
# ══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: Frontend builder ─────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend ./
RUN npm run build


# ── Stage 2: Python builder ───────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps for building native packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps into user local (isolated from system)
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Only runtime system deps needed by Python, Supervisor, and the Next server.
RUN apt-get update && apt-get install -y \
    supervisor \
    ca-certificates \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Copy the Node runtime only. The standalone Next build contains app deps.
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node

# Copy installed packages from builder stage
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .
RUN rm -rf frontend

# Copy standalone frontend runtime
COPY --from=frontend-builder /frontend/.next/standalone ./frontend
COPY --from=frontend-builder /frontend/.next/static ./frontend/.next/static
COPY --from=frontend-builder /frontend/public ./frontend/public

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV API_BASE_URL=http://127.0.0.1:8000
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

# Build identifier — pass via `--build-arg BUILD_REV=$GIT_SHA` (EasyPanel build args).
# Defaults to "unknown" so local builds still succeed.
ARG BUILD_REV=unknown
ENV BUILD_REV=${BUILD_REV}

# Expose frontend, API, and LiveKit agent ports
EXPOSE 3000 8000 8081

# Start services via supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
