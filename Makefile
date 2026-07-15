ROOT_DIR := $(abspath .)
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
INFRA_DIR := $(ROOT_DIR)/infra
COMPOSE := docker compose --env-file $(ROOT_DIR)/.env -f $(INFRA_DIR)/docker-compose.yml
PYTHON ?= python
PIP ?= pip

PIPELINE_DIR := $(ROOT_DIR)/data_pipeline

.PHONY: help infra-up infra-down backend-install frontend-install migrate seed backend frontend test lint format import-demo validate-sft dataset-install dataset-test dataset-bootstrap dataset-download dataset-parse dataset-label dataset-review-export dataset-review-priority dataset-validate dataset-build-rag dataset-build-agent dataset-build-sft dataset-report dataset-demo

help:
	@echo "BidPilot development commands"
	@echo "  make infra-up            Start Postgres/Redis/MinIO/Qdrant"
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
	@echo "  make validate-sft        Validate ShareGPT sample"
	@echo "  make dataset-install     Install data_pipeline package"
	@echo "  make dataset-test        Run data_pipeline tests"
	@echo "  make dataset-bootstrap   Bootstrap demo fixtures into datasets/"
	@echo "  make dataset-download    Resume pending downloads"
	@echo "  make dataset-parse       Parse -> clean -> chunk"
	@echo "  make dataset-label       Rule-based requirement labeling"
	@echo "  make dataset-review-export Export review CSV"
	@echo "  make dataset-validate    Validate all dataset artifacts"
	@echo "  make dataset-build-sft   Build ShareGPT SFT splits"
	@echo "  make dataset-report      Write dataset statistics reports"
	@echo "  make dataset-demo        Run full local demo pipeline"

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

lint:
	cd $(BACKEND_DIR) && ruff check app tests
	cd $(BACKEND_DIR) && mypy app

format:
	cd $(BACKEND_DIR) && ruff format app tests
	cd $(BACKEND_DIR) && ruff check --fix app tests

import-demo:
	$(PYTHON) $(ROOT_DIR)/scripts/import_demo_data.py

validate-sft:
	$(PYTHON) $(ROOT_DIR)/training/llamafactory/scripts/validate_sft_dataset.py \
		--dataset-file $(ROOT_DIR)/training/llamafactory/data/sample_sharegpt.json \
		--dataset-info $(ROOT_DIR)/training/llamafactory/data/dataset_info.json \
		--dataset-name bidpilot_sample_sharegpt

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

dataset-report:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data report

dataset-demo:
	cd $(PIPELINE_DIR) && $(PYTHON) -m bidpilot_data run-demo

