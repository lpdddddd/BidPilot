ROOT_DIR := $(abspath .)
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
INFRA_DIR := $(ROOT_DIR)/infra
COMPOSE := docker compose --env-file $(ROOT_DIR)/.env -f $(INFRA_DIR)/docker-compose.yml
PYTHON ?= python
PIP ?= pip

PIPELINE_DIR := $(ROOT_DIR)/data_pipeline

.PHONY: help infra-up infra-down backend-install frontend-install migrate seed backend frontend test lint format import-demo validate-sft validate-sft-sample validate-sft-real validate-sft-internal validate-sft-llamafactory validate-sft-smoke dataset-install dataset-test dataset-bootstrap dataset-download dataset-parse dataset-label dataset-review-export dataset-review-priority dataset-validate dataset-build-rag dataset-build-agent dataset-build-sft dataset-build-reference dataset-report dataset-demo rag-smoke rag-smoke-live llm-up

help:
	@echo "BidPilot development commands"
	@echo "  make infra-up            Start Postgres/Redis/MinIO/Qdrant/OpenSearch"
	@echo "  make infra-down          Stop infrastructure"
	@echo "  make backend-install     Install backend deps"
	@echo "  make frontend-install    Install frontend deps"
	@echo "  make migrate             Run Alembic migrations"
	@echo "  make seed                Placeholder seed (demo import)"
	@echo "  make backend             Run FastAPI"
	@echo "  make frontend            Run Vite dev server"
	@echo "  make test                Run backend tests"
	@echo "  make lint                Run ruff + mypy"
	@echo "  make format              Run ruff format"
	@echo "  make import-demo         Import demo pack"
	@echo "  make validate-sft        Alias of validate-sft-real (internal+LF preprocess)"
	@echo "  make validate-sft-sample Validate sample_sharegpt.json"
	@echo "  make validate-sft-internal Validate ShareGPT structure only"
	@echo "  make validate-sft-llamafactory Run real LLaMAFactory preprocess probe"
	@echo "  make validate-sft-real   Internal + LLaMAFactory preprocess (fails if LF missing)"
	@echo "  make dataset-install     Install data_pipeline package"
	@echo "  make dataset-test        Run data_pipeline tests"
	@echo "  make dataset-bootstrap   Bootstrap demo fixtures into datasets/"
	@echo "  make dataset-download    Resume pending downloads"
	@echo "  make dataset-parse       Parse -> clean -> chunk"
	@echo "  make dataset-label       Rule-based requirement labeling"
	@echo "  make dataset-review-export Export review CSV"
	@echo "  make dataset-validate    Validate all dataset artifacts"
	@echo "  make dataset-build-sft   Build ShareGPT SFT splits"
	@echo "  make dataset-build-reference  Build auto reference eval dataset"
	@echo "  make dataset-report      Write dataset statistics reports"
	@echo "  make rag-smoke           Mock RAG acceptance (no GPU / vLLM required)"
	@echo "  make rag-smoke-live      Live RAG smoke against running API + vLLM"
	@echo "  make llm-up              Print commands to start local Qwen3-8B vLLM"

infra-up:
	$(COMPOSE) up -d

infra-down:
	$(COMPOSE) down

backend-install:
	cd $(BACKEND_DIR) && $(PIP) install -e ".[dev]"

frontend-install:
	cd $(FRONTEND_DIR) && npm install

migrate:
	cd $(BACKEND_DIR) && alembic upgrade head

seed:
	$(PYTHON) $(ROOT_DIR)/scripts/import_demo_data.py --dry-run || true
	@echo "Use: make import-demo  (or pass --dry-run for stats only)"

backend:
	cd $(BACKEND_DIR) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd $(FRONTEND_DIR) && npm run dev -- --host 0.0.0.0 --port 5173

test:
	cd $(BACKEND_DIR) && pytest -q

rag-smoke:
	cd $(ROOT_DIR) && $(PYTHON) scripts/rag_smoke_accept.py

rag-smoke-live:
	cd $(ROOT_DIR) && RAG_SMOKE_LIVE=1 $(PYTHON) scripts/rag_smoke_accept.py

llm-up:
	@echo "Hub mode (no local mount):"
	@echo "  unset LLM_MODEL_PATH && ./scripts/serve_qwen3_vllm.sh"
	@echo "  docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.llm.yml --profile llm up -d"
	@echo "Local weights mode:"
	@echo "  export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B"
	@echo "  ./scripts/serve_qwen3_vllm.sh"
	@echo "  docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.llm.yml -f infra/docker-compose.llm.local.yml --profile llm up -d"
	@echo "Then set LLM_ENABLED=true and run: make rag-smoke-live"

lint:
	cd $(BACKEND_DIR) && ruff check app tests
	cd $(BACKEND_DIR) && mypy app

format:
	cd $(BACKEND_DIR) && ruff format app tests
	cd $(BACKEND_DIR) && ruff check --fix app tests

import-demo:
	$(PYTHON) $(ROOT_DIR)/scripts/import_demo_data.py

validate-sft-sample:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_dataset.py \
		--dataset-file $(ROOT_DIR)/training/llamafactory/data/sample_sharegpt.json \
		--dataset-info $(ROOT_DIR)/training/llamafactory/data/dataset_info.json \
		--dataset-name bidpilot_sample_sharegpt

validate-sft-internal:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_real.py \
		--repo-root $(ROOT_DIR) --mode internal

validate-sft-smoke:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_real.py \
		--repo-root $(ROOT_DIR) --mode all --max-samples 64

validate-sft-llamafactory:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_real.py \
		--repo-root $(ROOT_DIR) --mode llamafactory --all-samples

validate-sft-real:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_real.py \
		--repo-root $(ROOT_DIR) --mode all --all-samples

validate-sft: validate-sft-real

dataset-install:
	cd $(PIPELINE_DIR) && $(PIP) install -e ".[dev]"

dataset-test:
	cd $(PIPELINE_DIR) && pytest -q

dataset-bootstrap:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data bootstrap

dataset-download:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data download --resume

dataset-parse:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data parse --resume
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data clean --resume
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data chunk --resume

dataset-label:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data label requirements --mode rules --resume
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data label matches

dataset-review-export:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data review export

dataset-review-priority:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data review export-priority

dataset-validate:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data validate all

dataset-validate-rag:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data validate rag

dataset-build-rag:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data build-rag --limit 300

dataset-build-agent:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data build-agent --limit 500

dataset-build-sft:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data build-sft

dataset-build-reference:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data build-reference --seed 42

dataset-report:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data report

dataset-demo:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data run-demo

