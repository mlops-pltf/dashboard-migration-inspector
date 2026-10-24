# Build from the repo root:  docker build -f packages/data_agent/Dockerfile .
# AgentCore requires ARM64 images: add --platform linux/arm64 when building for deployment.
FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DOCKER_CONTAINER=1
WORKDIR /app

COPY packages/inspector_contract /app/packages/inspector_contract
COPY packages/data_agent /app/packages/data_agent
RUN pip install --no-cache-dir /app/packages/inspector_contract /app/packages/data_agent \
    && useradd --create-home --uid 10001 agent

USER agent
EXPOSE 9000
CMD ["python", "-m", "data_agent.data_agent"]
