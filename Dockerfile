FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY alembic.ini ./
COPY logging.yaml ./
COPY migrations/ ./migrations/
COPY config.example.yaml ./config.yaml
COPY docker-compose.yml ./
COPY compose.test.yml ./
COPY test.env ./
COPY scripts/ ./scripts/
COPY --from=web /web/dist ./frontend/dist
ENV CONFIG_PATH=/app/config.yaml
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/ready', timeout=4)" || exit 1
CMD ["sh", "-c", "alembic upgrade head && alembic check && exec uvicorn backend.main:app --host 0.0.0.0 --port 8080 --log-config logging.yaml"]
