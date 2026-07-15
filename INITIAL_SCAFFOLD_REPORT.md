# BidPilot Initial Scaffold Report

**Date:** 2026-07-15  
**Project path:** `/root/autodl-tmp/bidpilot`

## Layout decision

Workspace root `/root/autodl-tmp` already contains unrelated projects (`CoWeaver`, `wmdexpbt`).  
Therefore the BidPilot repository was created as **`bidpilot/`** (not nested as `bidpilot/bidpilot`). Existing files outside this directory were not modified or deleted.

External `bidpilot_demo_pack` was **not found**. A minimal local pack was placed under `demo_data/` for import / idempotency validation (synthetic scaffold data only).

---

## 1. Created directories and files

About **104** tracked scaffold files, including:

- `backend/` — FastAPI app, SQLAlchemy models, Alembic, pytest
- `frontend/` — React + TypeScript + Vite + Ant Design skeleton
- `data_pipeline/` — collectors/parsers/chunking/annotation/validation/evaluation stubs
- `training/llamafactory/` — QLoRA/LoRA configs, ShareGPT sample, export/validate scripts
- `datasets/{raw,interim,processed,gold,silver,eval}/`
- `demo_data/` — local demo JSON
- `infra/docker-compose.yml` + `infra/postgres/init.sql`
- `docs/ARCHITECTURE.md`, `docs/DATABASE.md`
- `.env.example`, `.gitignore`, `Makefile`, `README.md`
- `scripts/import_demo_data.py`, `scripts/init_db.sh`

---

## 2. Database tables

17 tables migrated:

1. `organizations`
2. `users`
3. `organization_members`
4. `bid_projects`
5. `documents`
6. `document_versions`
7. `document_chunks` (includes reserved `qdrant_point_id`)
8. `requirements`
9. `evidence_links`
10. `company_profiles`
11. `requirement_matches`
12. `requirement_match_evidence`
13. `conversations`
14. `messages`
15. `agent_runs`
16. `agent_steps`
17. `tool_calls`

---

## 3. API inventory

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | `{"status":"ok"}` |
| GET | `/ready` | Checks Postgres / Redis / MinIO / Qdrant |
| POST | `/api/v1/projects` | Create project (+ auto org) |
| GET | `/api/v1/projects` | List projects |
| GET | `/api/v1/projects/{project_id}` | Project detail |
| POST | `/api/v1/projects/{project_id}/documents` | Register document metadata |
| GET | `/api/v1/projects/{project_id}/documents` | List project documents |

OpenAPI: `/docs`

---

## 4. Alembic migration result

- Revision: `a34e7a76f341_initial_schema`
- Status: **applied successfully** to local PostgreSQL 14  
  (`postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot`)
- Command: `alembic upgrade head` / `make migrate`
- `alembic current` → `a34e7a76f341 (head)`

Note: local PG was started manually for this environment because Docker daemon could not start.

---

## 5. Test results

Commands run:

```bash
ruff format / ruff check   # passed
mypy app                   # Success: no issues found in 41 source files
pytest -q                  # 13 passed
```

Coverage includes:

1. health (+ ready degraded mock)
2. model table / qdrant_point_id / CRUD
3. project create/list/get
4. document metadata create/list
5. document_versions / document_chunks unique constraints
6. demo import idempotency + dry-run
7. ShareGPT sample validation vs `dataset_info.json`
8. train/test project leakage check in exporter

---

## 6. Docker service status

| Check | Result |
| --- | --- |
| `docker compose ... config` | **OK** (Compose v2.40.3; YAML validates) |
| `docker compose up` | **Failed** — Docker daemon cannot start in this host (`iptables` / bridge NAT permission error) |
| OpenSearch | Not started (by design); documented as future BM25 backend |

Compose services defined: `postgres`, `redis`, `minio`, `minio-init` (creates `bidpilot-documents`), `qdrant`.

---

## 7. Demo data import result

```text
demo_root: demo_data
dry_run: true
organizations created: 1
projects created: 1
requirements created: 2
company_profiles created: 2
requirement_matches created: 2
ok: true
```

Idempotency verified by pytest (`test_demo_import_idempotent`).  
Original requirement UUIDs preserved.

---

## 8. LLaMAFactory integration status

| Item | Status |
| --- | --- |
| Config templates (`qwen3_8b_qlora_sft.yaml`, `qwen3_8b_lora_sft.yaml`) | Done |
| ShareGPT sample + `dataset_info.json` | Done / validated (`ok: true`, 3 records) |
| `export_sft_dataset.py` | Done (split + leak guard + stats) |
| `validate_sft_dataset.py` | Done |
| Backend imports `llamafactory` | No |
| LoRA training started | No (intentionally) |

---

## 9. Current blockers

1. **Docker daemon unavailable** on this machine → cannot bring up Redis/MinIO/Qdrant containers here.
2. **Node.js / npm not installed** → frontend dependencies not installed; sources are ready (`frontend/`).
3. Original **`bidpilot_demo_pack` not present** → used scaffold `demo_data/` instead.
4. No paid model calls / no large crawl / no training run (by scope).

---

## 10. Next-stage recommendations

1. Enable Docker (or managed services) and run `make infra-up` for Redis/MinIO/Qdrant.
2. Install Node 18+ and `make frontend-install && make frontend`.
3. Implement document upload to MinIO + parser pipeline in `data_pipeline/`.
4. Implement Qdrant indexing from `document_chunks` using `qdrant_point_id`.
5. Add OpenSearch for BM25 and wire `BM25SearchPort`.
6. Implement LangGraph agent against `agent_runs` / `agent_steps` / `tool_calls`.
7. Scale SFT dataset export from gold annotations and train Qwen QLoRA via external `LLAMAFACTORY_HOME`.

---

## Execution checklist (this round)

| Step | Result |
| --- | --- |
| 1. Format | Passed |
| 2. Static checks (ruff + mypy) | Passed |
| 3. Unit/integration tests | 13 passed |
| 4. Docker Compose config check | Passed |
| 5. Alembic migration check | Passed (`head`) |
| 6. Demo dry-run | Passed |
| 7. LLaMAFactory sample validation | Passed |
