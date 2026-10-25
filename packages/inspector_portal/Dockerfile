# Build from the repo root:  docker build -f packages/inspector_portal/Dockerfile .
# One image, two services: the API (default command) and the UI (streamlit run ...; see docker-compose.yml).
FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DOCKER_CONTAINER=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR /app

COPY packages/inspector_contract /app/packages/inspector_contract
COPY packages/inspector_portal /app/packages/inspector_portal
RUN pip install --no-cache-dir /app/packages/inspector_contract /app/packages/inspector_portal \
    && useradd --create-home --uid 10001 portal \
    && mkdir -p /data /uploads && chown portal /data /uploads

USER portal
EXPOSE 8080 8501
CMD ["python", "-m", "inspector_portal.api"]
