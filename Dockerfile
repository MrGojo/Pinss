# Single-service image: FastAPI serves /api/* and the React production build at /
# Build from repo root: docker build -t pinterest-bott .

FROM node:18-alpine AS frontend
WORKDIR /app/frontend
# Reliable installs on slow CI networks; skip source maps for smaller/faster builds
ENV CI=false \
    GENERATE_SOURCEMAP=false \
    DISABLE_ESLINT_PLUGIN=true
COPY frontend/package.json frontend/yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 300000
COPY frontend/ ./
# Same-origin API (browser calls /api). Override at build time if frontend is hosted separately.
ARG REACT_APP_BACKEND_URL=
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
RUN yarn build

# Bookworm: stable package names (latest slim may use Trixie with different -dev packages)
FROM python:3.11-slim-bookworm AS backend
WORKDIR /app
# Native deps: jq (transitive), lxml wheels or source build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjq-dev \
    libonig-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./backend/
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/build ./backend/static
ENV FRONTEND_STATIC_DIR=/app/backend/static
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
