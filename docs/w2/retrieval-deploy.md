# Deploying guideline retrieval

Short version: **nothing about retrieval is fetched at runtime.** The
corpus, the chunk index, the ONNX weights and the chunk vectors are all in
the image, and the process warms them before it serves anything.

This note covers what that takes and what to check after a deploy. The
model choice and the numbers behind it are
[D25](DECISIONS.md) and [`retrieval-bakeoff.md`](retrieval-bakeoff.md).

## What ships

| Thing | Where | Size | Built by |
|---|---|---|---|
| Corpus Markdown, 28 documents | `agent/app/retrieval/corpus/documents/` | ~600 KB | committed |
| Chunk index, 693 chunks | `agent/app/retrieval/corpus/chunks.json` | ~1.6 MB | committed |
| Source registry / licensing | `agent/app/retrieval/corpus/sources.yaml` | ~14 KB | committed |
| Embedder `BAAI/bge-small-en-v1.5` | `/opt/models` in the image | ~67 MB | `docker build` |
| Cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` | `/opt/models` in the image | ~80 MB | `docker build` |
| Chunk vectors (693 × 384 float32) | `agent/app/retrieval/corpus/.cache/*.npz` | ~1 MB | `docker build` |

The two model directories are why the image grows by roughly 150 MB. That
is the cost of not downloading them on the deployment host, which is the
right trade for a container that has no business reaching huggingface.co.

## Model weights: build time, never runtime

`agent/Dockerfile` downloads both models into `/opt/models` and then sets
`HF_HUB_OFFLINE=1`. The ordering is the whole mechanism:

- **Before** that line, the download must succeed, or the build fails.
- **After** it, any model load reads `/opt/models` or raises. It cannot
  quietly reach the network, and it cannot quietly fall back to a slow
  path — a missing weight is a startup error, in the build, on a laptop,
  where someone is watching.

`AGENT_MODEL_CACHE_DIR=/opt/models` is set as an image `ENV` and read by
`app/retrieval/dense.py::model_cache_dir()`. Both `FastEmbedEmbedder` and
`LocalCrossEncoder` pass it to fastembed as `cache_dir`. Unset — which is
the case on a dev machine — means fastembed's own default under `$HOME`,
and the first run downloads.

## Vector cache

`app/retrieval/corpus/.cache/<model>.<key>.npz`, where `<key>` is a hash of
`(model name, corpus documents_sha256)`. Gitignored: it is derived data.

It is baked at image build by the same `RUN` layer, because **the runtime
root filesystem is read-only** (`deploy/docker-compose.agent.yml` sets
`read_only: true`). A cache miss in the container therefore cannot be
written back. `DenseIndex` treats that as a slow start rather than a
failure — the vectors it just computed are correct, the request is served,
and the `OSError` from the unwritable path is swallowed deliberately.

The key is what makes a stale cache unreadable rather than wrong: change a
corpus document and `documents_sha256` changes, so the old `.npz` is simply
never looked up.

## Preload at startup

`AGENT_PRELOAD_RETRIEVAL=true` (set in `deploy/docker-compose.agent.yml`)
makes the FastAPI lifespan call `app/retrieval/preload.py::preload_retrieval()`
before the first request: it loads the index, constructs both models,
builds the BM25 postings, reads the vector cache, and runs one real query
through both stages so the first clinician question is not also the first
ONNX inference of the process.

It is off by default so that a dev process and the unit suite do not load
ONNX they will not use.

**A preload failure does not stop the service.** The rest of the API does
not depend on retrieval, and refusing to serve a Pre-Visit Brief because a
guideline index would not warm is the wrong trade. It logs
`retrieval_preload_failed_event` with the exception type — and only the
type, because an exception string can carry content (CLAUDE.md invariant
1).

## After a deploy, check these

```bash
# 1. The preload ran, found its caches, and did not fetch anything.
docker compose -f docker-compose.agent.yml logs agent \
  | grep retrieval_preload_event
```

The line carries `documents`, `chunks`, `embed_model`, `reranker`,
`model_cache_dir`, `vector_cache_prebuilt` and `elapsed_ms`. Three things
to read on it:

- `vector_cache_prebuilt: true` — **false means the image did not bake the
  vectors** and every process start is re-embedding 693 chunks. It will
  still work. It is still a defect.
- `model_cache_dir: "/opt/models"` — `null` means the image `ENV` is not
  reaching the process and fastembed is looking under `$HOME`, which on
  this container is a tmpfs.
- `elapsed_ms` — on an M5 Pro with warm caches this is ~850 ms. On the
  droplet expect several times that. If it is in the tens of seconds, the
  vector cache is missing.

```bash
# 2. No new listening port. F-54: check, never assume.
ss -tlnp
```

Retrieval adds no port and no outbound dependency.

```bash
# 3. Retrieval events look sane under load.
docker compose -f docker-compose.agent.yml logs agent \
  | grep evidence_retrieval_event | tail -5
```

`hit_count`, `retrieval_scores`, `rerank_scores`, `reranked`,
`lexical_ms` / `dense_ms` / `fusion_ms` / `rerank_ms` / `total_ms`,
`guideline_ids`, `dated_hit_count`. No question text, no chunk text — that
is invariant 1 and `tests/test_retrieval_worker.py` asserts it.

**Watch `rerank_over_budget`.** D41 gives the rerank stage 4 s (D25 set 2.5 s); past
that the fused order stands, the output says `reranked=false`, and the
retriever opens a circuit so the next turns do not pay the same cost to
throw the same answer away. Since D41 the circuit closes itself after 60 s
or 10 skipped turns, logs `rerank_circuit_opened_event` and
`rerank_circuit_closed_event`, and a skipped turn reports
`rerank_skipped_circuit_open` rather than `rerank_over_budget`. So a steady
stream of `rerank_over_budget: true` now means reranks that really ran
long: the cross-encoder does not fit the host, and that is the D25 re-open
condition, not something to tune around.

## What has not been measured

Everything timed in `retrieval-bakeoff.md` is an Apple M5 Pro. The
cross-encoder is ~350 ms p50 there over a 20-candidate pool and it is the
number most likely to change on a shared vCPU. Nothing in this note has
been run on the Vultr host.

**Update 2026-09-22 — measured on the Vultr host** ([`DEPLOY.md`](DEPLOY.md)
§8, D41): preload 2.6–2.9 s with `vector_cache_prebuilt: true`; hybrid
retrieval p50 7.8 ms. The rerank stage is p50 1.38 s / max 2.28 s on an idle
host, and p50 2.45 s / max 4.09 s while OCR runs in the same container —
43% over 2.5 s there, 1.7% over 4 s. **The budget is now 4 s (D41).** An
earlier "9 of 60 over 2.5 s" was a miscount: after the first slow rerank the
circuit opens, and the skipped turns also report `over_budget: true`.
