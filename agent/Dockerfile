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
    HOME=/tmp \
    AGENT_MODEL_CACHE_DIR=/opt/models

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# --- guideline retrieval: weights and vectors are baked in, not fetched ---
#
# The runtime root filesystem is read-only and the container has no reason
# to reach huggingface.co, so both ONNX models and the 693 chunk vectors
# are produced here, at build time. Three steps, in order: download the
# weights into AGENT_MODEL_CACHE_DIR; confirm the committed chunk index is
# the one this corpus builds; warm the vector cache beside it.
#
# HF_HUB_OFFLINE is set *after* this. Before it the download must work;
# after it, any runtime model load reads /opt/models or fails loudly,
# instead of silently going to the network or silently running slow.
# See docs/w2/retrieval-deploy.md.
RUN python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; from app.retrieval.dense import DEFAULT_EMBED_MODEL; from app.retrieval.rerank import DEFAULT_CROSS_ENCODER; TextEmbedding(DEFAULT_EMBED_MODEL, cache_dir='/opt/models'); TextCrossEncoder(DEFAULT_CROSS_ENCODER, cache_dir='/opt/models')" \
 && python -m app.retrieval.build --check \
 && python -c "from app.retrieval.preload import preload_retrieval; preload_retrieval()" \
 && chmod -R a+rX /opt/models /app/app/retrieval/corpus

ENV HF_HUB_OFFLINE=1

# Unprivileged, and the compose file also mounts the root filesystem read-only.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin agent
USER 10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
