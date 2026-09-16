# Clinical Co-Pilot agent service. Run with agent/deploy/docker-compose.agent.yml
# (agent/docs/deployment.md), which puts it behind a TLS proxy and never
# publishes this container's port.
#
# The image holds no credentials: ANTHROPIC_API_KEY is supplied at runtime,
# and each clinician's FHIR token arrives per request (ARCHITECTURE.md §2).
# agent/.dockerignore keeps .env files out of the build context.

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Unprivileged, and the compose file also mounts the root filesystem read-only.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin agent
USER 10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
