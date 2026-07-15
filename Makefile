ROOT_DIR := $(abspath .)
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
INFRA_DIR := $(ROOT_DIR)/infra
COMPOSE := docker compose --env-file $(ROOT_DIR)/.env -f $(INFRA_DIR)/docker-compose.yml
PYTHON ?= python
PIP ?= pip

.PHONY: help infra-up infra-down backend-install frontend-install migrate seed backend frontend test lint format import-demo validate-sft

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
