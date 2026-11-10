# Two images, built by docker-compose.yml through their targets:
#   backend    API, paper trading and data collection
#   frontend   the dashboard, served by nginx, which forwards /api and /auth to the backend
#
# The app has no login of its own. Publish only the frontend, and only to localhost.

FROM node:24-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS frontend
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /build/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]

FROM python:3.11-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALGOVERSE_HOST=0.0.0.0 \
    ALGOVERSE_DATA_DIR=/data
WORKDIR /app

# Dependencies install from a stub package so that code edits do not reinstall them.
COPY pyproject.toml ./
RUN mkdir -p backend/algoverse \
    && touch backend/algoverse/__init__.py \
    && pip install --no-cache-dir --editable .

COPY backend/ ./backend/
COPY config/ ./config/

RUN useradd --create-home --uid 1000 algoverse \
    && mkdir /data \
    && chown algoverse:algoverse /data
USER algoverse
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

ENTRYPOINT ["algoverse"]
CMD ["serve"]
