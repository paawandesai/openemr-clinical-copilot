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
    AGENT_MODEL_CACHE_DIR=/opt/models \
    OMP_THREAD_LIMIT=1

# OMP_THREAD_LIMIT=1: Debian's Tesseract is built with OpenMP and starts one
# thread per core per process. Two OCR jobs at once on the 4-vCPU host then
# fight each other: ~162 s per page, against 5.6 s with the limit (measured
# on the host, docs/w2/DEPLOY.md §8). ONNX Runtime uses its own thread pool,
# not OpenMP, so reranking is unaffected.

WORKDIR /app

# OCR for image-only pages (app/extraction/words.py, D3 stage 1). pytesseract
# is only a wrapper: the binary and its English language data come from the
# OS, and pip installs neither. Without them a scanned page raises
# OcrUnavailableError at request time, so the build checks for them below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Fail the build, not the first scanned upload, if the OCR stack or the PDF
# and image libraries are missing: `eng` must be a listed language, and the
# extraction module must see the binary through pytesseract.
RUN tesseract --list-langs 2>/dev/null | grep -qx eng \
 && python -c "import pymupdf, PIL; from app.extraction.words import tesseract_available; assert tesseract_available(), 'tesseract not usable from app.extraction.words'"

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
