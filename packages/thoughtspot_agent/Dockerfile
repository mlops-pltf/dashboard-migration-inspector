# Build from the repo root:  docker build -f packages/thoughtspot_agent/Dockerfile .
# AgentCore requires ARM64 images: add --platform linux/arm64 when building for deployment.
FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DOCKER_CONTAINER=1 PACKAGE_ROOT=/packages
WORKDIR /app

COPY packages/inspector_contract /app/packages/inspector_contract
COPY packages/thoughtspot_agent /app/packages/thoughtspot_agent
RUN pip install --no-cache-dir /app/packages/inspector_contract /app/packages/thoughtspot_agent \
    && useradd --create-home --uid 10001 agent && mkdir -p /packages

USER agent
EXPOSE 9000
CMD ["python", "-m", "thoughtspot_agent.thoughtspot_agent"]
