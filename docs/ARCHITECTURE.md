# BidPilot Architecture

## Service responsibilities

| Component | Responsibility |
| --- | --- |
| `backend/` (FastAPI) | Business APIs, metadata CRUD, readiness checks, orchestration stubs |
| `frontend/` (React) | Project / document UI shell |
| `data_pipeline/` | Future collectors, parsers, chunking, annotation, validation, evaluation |
| `training/llamafactory/` | Offline SFT export / validation / LLaMAFactory configs only |
| PostgreSQL | Relational business data and file metadata |
| MinIO | Original document binary storage |
| Qdrant | Dense vector index (future RAG) |
| Redis | Cache / job coordination (future) |
| OpenSearch | Reserved for BM25 sparse retrieval (not started in this scaffold) |

## Data flow (scaffold)

```text
Browser -> FastAPI (/api/v1)
             |-> PostgreSQL (projects, documents metadata, requirements, agents)
             |-> MinIO (file bytes via storage_bucket/storage_key)
             |-> Redis / Qdrant (checked by /ready; domain features later)

Annotation JSONL -> training/llamafactory/scripts/export_sft_dataset.py
                 -> ShareGPT messages JSON
                 -> external LLaMAFactory CLI (LLAMAFACTORY_HOME)
```

## File storage

1. Uploaded / ingested tender files are stored in MinIO bucket `MINIO_BUCKET`.
2. PostgreSQL `documents` / `document_versions` store only metadata: bucket, key, checksum, mime, parse status.
3. Application code must not persist file binaries in PostgreSQL.

## Database vs vector store

- PostgreSQL: organizations, users, projects, document metadata, chunks text, requirements, evidence links, company profiles, matches, conversations, agent runs.
- Qdrant: embedding vectors keyed by `document_chunks.qdrant_point_id` (column reserved; embeddings not stored in PG).
- OpenSearch (future): BM25 inverted index over chunk text.

## RAG reserved interfaces

See `backend/app/rag/interfaces.py`:

- `DenseSearchPort` → Qdrant
- `BM25SearchPort` → OpenSearch (future)
- `HybridSearchPort` → fusion

## Agent reserved interfaces

See `backend/app/agents/interfaces.py`:

- `AgentPort.run(AgentRequest) -> AgentResult`
- Persistence tables: `agent_runs`, `agent_steps`, `tool_calls`

LangGraph graphs are intentionally not implemented in this scaffold.

## LLaMAFactory integration

1. Keep LLaMAFactory outside the repo (`LLAMAFACTORY_HOME`).
2. Backend never imports `llamafactory`.
3. Export ShareGPT `messages` JSON via `training/llamafactory/scripts/export_sft_dataset.py`.
4. Validate with `validate_sft_dataset.py` before training.
5. Launch training manually with `llamafactory-cli train` against YAML under `training/llamafactory/configs/`.

## Module isolation

- Business API code lives in `backend/`.
- Heavy data engineering lives in `data_pipeline/` and `datasets/`.
- Model fine-tuning artefacts live in `training/`.
